"""
Blueprint-based question selection service for SQE1 practice modes.

This module implements:
- Largest-remainder proportional allocation
- Hard min/max enforcement for strict-mode tests (90+ questions)
- Cross-cutting (Ethics/PC/ML) cap enforcement
- Coverage checking and shortage redistribution
- Adaptive Bayesian-weighted SQE selection
- Random-seed storage for audit reproducibility
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from flask import current_app
from sqlalchemy import and_, func, select

from ..extensions import db
from ..models.question import (
    Question,
    QuestionBankImport,
    FORMAT_SQE5,
    FORMAT_LEGACY_MCQ4,
    REVIEW_STATUS_OFFICIAL,
    REVIEW_STATUS_REVIEWED,
)
from ..models.specification import BlueprintProfile, BlueprintSubject
from ..models.sqe import UserSubjectStat
from ..models.subject import Tag


# ---------------------------------------------------------------------------
# Configuration constants (all configurable via app.config)
# ---------------------------------------------------------------------------

# Adaptive weighting constants
ADAPTIVE_PRIOR_CORRECT = 2
ADAPTIVE_PRIOR_TOTAL = 4
ADAPTIVE_RECENT_WEIGHT = 0.65
ADAPTIVE_LIFETIME_WEIGHT = 0.35
ADAPTIVE_EVIDENCE_THRESHOLD = 20
ADAPTIVE_MULTIPLIER_MIN = 0.5
ADAPTIVE_MULTIPLIER_MAX = 3.0
# Minimum questions for enforcing strict blueprint bounds
STRICT_MIN_DEFAULT = 30


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


@dataclass
class SubjectAllocation:
    subject_id: int
    subject_code: str
    target_pct: float
    min_pct: float
    max_pct: float
    ideal: float        # requested_count * target_pct
    allocated: int = 0  # final integer allocation
    available: int = 0  # questions available in DB
    shortage: int = 0   # allocated - available (0 if satisfied)


@dataclass
class BlueprintAllocationResult:
    profile_id: int
    paper: str
    requested: int
    actual: int
    allocations: list[SubjectAllocation] = field(default_factory=list)
    cross_cutting_count: int = 0
    cross_cutting_pct: float = 0.0
    cross_cutting_cap: float = 0.20
    is_strict: bool = False
    is_approximate: bool = False
    shortfall_by_subject: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    seed: Optional[int] = None


@dataclass
class SelectedQuestion:
    question_id: int
    subject_id: Optional[int]
    subject_code: Optional[str]
    display_position: int


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def select_sqe_questions(
    profile: BlueprintProfile,
    count: int,
    user_id: int,
    mode: str,
    seed: Optional[int] = None,
    exclude_question_ids: Optional[list[int]] = None,
    strict: bool = True,
) -> tuple[list[SelectedQuestion], BlueprintAllocationResult]:
    """
    Select questions for an SQE practice test using the given blueprint profile.

    Args:
        profile: BlueprintProfile to use for allocation
        count: number of questions to select
        user_id: user requesting the test (for adaptive mode)
        mode: 'sqe_blueprint' | 'sqe_adaptive' | 'sqe_simulation'
        seed: random seed (generated if None)
        exclude_question_ids: question IDs recently used (to avoid repetition)
        strict: if True, enforce hard min/max bounds when count >= STRICT_MIN

    Returns:
        (selected_questions, allocation_result)
    """
    if seed is None:
        seed = random.randint(0, 2**31 - 1)

    rng = random.Random(seed)
    exclude_ids = set(exclude_question_ids or [])

    # ------------------------------------------------------------------
    # Load blueprint subjects
    # ------------------------------------------------------------------
    bp_subjects: list[BlueprintSubject] = db.session.execute(
        select(BlueprintSubject)
        .where(BlueprintSubject.profile_id == profile.id)
        .join(BlueprintSubject.subject)
    ).scalars().all()

    if not bp_subjects:
        raise ValueError(f"Blueprint profile {profile.id} has no subjects configured.")

    strict_min = getattr(profile, "strict_min_questions", STRICT_MIN_DEFAULT)
    use_strict = strict and count >= strict_min

    cross_cutting_cap = float(getattr(profile, "cross_cutting_cap", Decimal("0.20")))
    cross_cutting_tag_ids = _get_cross_cutting_tag_ids()

    # ------------------------------------------------------------------
    # Get available question counts per subject
    # ------------------------------------------------------------------
    subject_ids = [bs.subject_id for bs in bp_subjects]
    available_counts = _get_available_counts(
        paper=profile.paper,
        subject_ids=subject_ids,
        exclude_ids=exclude_ids,
    )

    # ------------------------------------------------------------------
    # Compute allocation
    # ------------------------------------------------------------------
    allocations: list[SubjectAllocation] = []
    for bs in bp_subjects:
        sa = SubjectAllocation(
            subject_id=bs.subject_id,
            subject_code=getattr(bs.subject, "code", str(bs.subject_id)),
            target_pct=float(bs.target_pct),
            min_pct=float(bs.min_pct),
            max_pct=float(bs.max_pct),
            ideal=count * float(bs.target_pct),
            available=available_counts.get(bs.subject_id, 0),
        )
        allocations.append(sa)

    if use_strict:
        _allocate_strict(allocations, count, rng)
    else:
        _allocate_largest_remainder(allocations, count, rng)

    # Handle shortages via redistribution (non-strict mode only)
    result = BlueprintAllocationResult(
        profile_id=profile.id,
        paper=profile.paper,
        requested=count,
        actual=0,
        allocations=allocations,
        cross_cutting_cap=cross_cutting_cap,
        is_strict=use_strict,
        seed=seed,
    )

    if use_strict:
        for sa in allocations:
            if sa.allocated > sa.available:
                result.shortfall_by_subject[sa.subject_code] = sa.allocated - sa.available
                result.warnings.append(
                    f"{sa.subject_code}: requires {sa.allocated} but only {sa.available} available."
                )
                sa.shortage = sa.allocated - sa.available
        if result.shortfall_by_subject:
            # Cannot satisfy strict allocation — caller should inform user
            result.actual = sum(sa.available for sa in allocations)
            result.warnings.insert(
                0,
                "Strict blueprint allocation cannot be met. Showing maximum available questions.",
            )
    else:
        _redistribute_shortages(allocations, count, rng)
        for sa in allocations:
            if sa.allocated > sa.available:
                sa.shortage = sa.allocated - sa.available
                result.is_approximate = True

    result.actual = sum(min(sa.allocated, sa.available) for sa in allocations)

    # ------------------------------------------------------------------
    # Adaptive adjustment (for sqe_adaptive mode)
    # ------------------------------------------------------------------
    if mode == "sqe_adaptive":
        adaptive_weights = _compute_adaptive_weights(
            user_id=user_id,
            allocations=allocations,
            rng=rng,
        )
        for sa in allocations:
            sa.allocated = adaptive_weights.get(sa.subject_id, sa.allocated)

    # ------------------------------------------------------------------
    # Select questions per subject
    # ------------------------------------------------------------------
    selected: list[SelectedQuestion] = []
    cross_cutting_question_ids: set[int] = set()

    # Shuffle subject order for variety, seeded
    shuffled_allocations = list(allocations)
    rng.shuffle(shuffled_allocations)

    for sa in shuffled_allocations:
        if sa.allocated <= 0:
            continue
        n_to_pick = min(sa.allocated, sa.available)
        if n_to_pick <= 0:
            continue

        questions = _fetch_questions_for_subject(
            paper=profile.paper,
            subject_id=sa.subject_id,
            limit=n_to_pick,
            exclude_ids=exclude_ids | {q.question_id for q in selected},
            rng=rng,
        )
        for q_id, has_cc_tag in questions:
            if has_cc_tag:
                cross_cutting_question_ids.add(q_id)
            selected.append(
                SelectedQuestion(
                    question_id=q_id,
                    subject_id=sa.subject_id,
                    subject_code=sa.subject_code,
                    display_position=len(selected),
                )
            )

    # ------------------------------------------------------------------
    # Cross-cutting cap enforcement
    # ------------------------------------------------------------------
    cc_count = len(cross_cutting_question_ids.intersection({q.question_id for q in selected}))
    cc_pct = cc_count / len(selected) if selected else 0.0

    if cc_pct > cross_cutting_cap + 0.01:  # 1% tolerance
        # Remove excess cross-cutting questions from the back
        non_cc = [q for q in selected if q.question_id not in cross_cutting_question_ids]
        cc_qs = [q for q in selected if q.question_id in cross_cutting_question_ids]
        max_cc = math.floor(len(selected) * cross_cutting_cap)
        selected = cc_qs[:max_cc] + non_cc
        rng.shuffle(selected)
        result.warnings.append(
            f"Cross-cutting questions trimmed to {max_cc} ({cross_cutting_cap*100:.0f}% cap)."
        )

    # Renumber display positions
    rng.shuffle(selected)
    for i, q in enumerate(selected):
        q.display_position = i

    result.actual = len(selected)
    result.cross_cutting_count = cc_count
    result.cross_cutting_pct = cc_pct

    if result.is_approximate:
        result.warnings.append(
            "Test allocation is approximate due to insufficient questions in some subjects."
        )

    return selected, result


def check_blueprint_coverage(
    profile: BlueprintProfile,
    count: int,
    strict: bool = False,
) -> dict[str, int]:
    """
    Returns {subject_code: shortfall} for subjects that cannot meet the allocation.
    Empty dict means full coverage available.
    """
    bp_subjects: list[BlueprintSubject] = db.session.execute(
        select(BlueprintSubject).where(BlueprintSubject.profile_id == profile.id)
    ).scalars().all()

    subject_ids = [bs.subject_id for bs in bp_subjects]
    available = _get_available_counts(profile.paper, subject_ids, set())
    shortfalls: dict[str, int] = {}

    for bs in bp_subjects:
        code = getattr(bs.subject, "code", str(bs.subject_id))
        needed = math.floor(count * float(bs.target_pct))
        have = available.get(bs.subject_id, 0)
        if have < needed:
            shortfalls[code] = needed - have

    return shortfalls


# ---------------------------------------------------------------------------
# Private allocation helpers
# ---------------------------------------------------------------------------


def _allocate_largest_remainder(
    allocations: list[SubjectAllocation],
    total: int,
    rng: random.Random,
) -> None:
    """Fill allocations using largest-remainder algorithm."""
    # Floor each allocation
    for sa in allocations:
        sa.allocated = math.floor(sa.ideal)

    remainder = total - sum(sa.allocated for sa in allocations)
    if remainder <= 0:
        return

    # Sort by descending fractional remainder; deterministic tie-break via shuffle
    fractions = [(sa.ideal - math.floor(sa.ideal), i) for i, sa in enumerate(allocations)]
    # Shuffle first for randomness, then sort (stable sort preserves shuffle order for equal fracs)
    idx_list = list(range(len(allocations)))
    rng.shuffle(idx_list)
    idx_list.sort(key=lambda i: -(allocations[i].ideal - math.floor(allocations[i].ideal)))

    for i in range(remainder):
        allocations[idx_list[i]].allocated += 1


def _allocate_strict(
    allocations: list[SubjectAllocation],
    total: int,
    rng: random.Random,
) -> None:
    """Allocate using strict blueprint bounds (min/max percentage)."""
    # Compute min/max integer bounds
    for sa in allocations:
        sa_min = math.ceil(total * sa.min_pct)
        sa_max = math.floor(total * sa.max_pct)
        sa.allocated = max(sa_min, min(sa_max, round(sa.ideal)))

    # Adjust to reach total
    _adjust_to_total(allocations, total, rng)


def _adjust_to_total(
    allocations: list[SubjectAllocation], total: int, rng: random.Random
) -> None:
    """Nudge allocations up or down to sum to total, respecting available counts."""
    current = sum(sa.allocated for sa in allocations)
    diff = total - current

    shuffled = list(range(len(allocations)))
    rng.shuffle(shuffled)

    if diff > 0:
        for i in shuffled:
            if diff <= 0:
                break
            sa = allocations[i]
            max_add = min(diff, math.floor(total * sa.max_pct) - sa.allocated)
            if max_add > 0:
                sa.allocated += max_add
                diff -= max_add
    elif diff < 0:
        for i in shuffled:
            if diff >= 0:
                break
            sa = allocations[i]
            max_sub = sa.allocated - math.ceil(total * sa.min_pct)
            if max_sub > 0:
                reduce = min(-diff, max_sub)
                sa.allocated -= reduce
                diff += reduce


def _redistribute_shortages(
    allocations: list[SubjectAllocation],
    total: int,
    rng: random.Random,
) -> None:
    """Redistribute unfillable allocations to subjects below their max."""
    shortage_total = 0
    for sa in allocations:
        if sa.allocated > sa.available:
            shortage = sa.allocated - sa.available
            sa.allocated = sa.available
            shortage_total += shortage

    if shortage_total == 0:
        return

    # Find subjects with headroom (below their max bound)
    candidates = [
        sa for sa in allocations
        if sa.available > sa.allocated
    ]
    if not candidates:
        return

    rng.shuffle(candidates)
    for sa in candidates:
        if shortage_total <= 0:
            break
        headroom = min(sa.available - sa.allocated, shortage_total)
        sa.allocated += headroom
        shortage_total -= headroom


# ---------------------------------------------------------------------------
# Adaptive weighting
# ---------------------------------------------------------------------------


def _compute_adaptive_weights(
    user_id: int,
    allocations: list[SubjectAllocation],
    rng: random.Random,
) -> dict[int, int]:
    """
    Return {subject_id: adjusted_allocation} using Bayesian accuracy smoothing.
    Keeps total allocation sum constant.
    """
    total = sum(sa.allocated for sa in allocations)
    if total == 0:
        return {}

    prior_c = current_app.config.get("ADAPTIVE_PRIOR_CORRECT", ADAPTIVE_PRIOR_CORRECT)
    prior_t = current_app.config.get("ADAPTIVE_PRIOR_TOTAL", ADAPTIVE_PRIOR_TOTAL)
    recent_w = current_app.config.get("ADAPTIVE_RECENT_WEIGHT", ADAPTIVE_RECENT_WEIGHT)
    lifetime_w = current_app.config.get("ADAPTIVE_LIFETIME_WEIGHT", ADAPTIVE_LIFETIME_WEIGHT)
    evidence_thr = current_app.config.get("ADAPTIVE_EVIDENCE_THRESHOLD", ADAPTIVE_EVIDENCE_THRESHOLD)

    # Load user stats
    subject_ids = [sa.subject_id for sa in allocations]
    stats_rows = db.session.execute(
        select(UserSubjectStat).where(
            UserSubjectStat.user_id == user_id,
            UserSubjectStat.subject_id.in_(subject_ids),
        )
    ).scalars().all()
    stats_by_subject = {s.subject_id: s for s in stats_rows}

    raw_weights: dict[int, float] = {}
    for sa in allocations:
        stat = stats_by_subject.get(sa.subject_id)
        if stat:
            lifetime_acc = (stat.correct + prior_c) / (stat.attempts + prior_t)
            recent_acc = (stat.recent_correct + prior_c) / (stat.recent_attempts + prior_t)
            evidence = min(1.0, stat.attempts / evidence_thr)
            combined_obs = recent_w * recent_acc + lifetime_w * lifetime_acc
            effective_acc = evidence * combined_obs + (1 - evidence) * 0.5
        else:
            effective_acc = 0.5  # cold start — equal weight

        weakness = 1.0 - effective_acc
        # Raw adaptive weight = blueprint_target * adaptive_multiplier
        # multiplier = 1 + weakness (ranges from 1.0 for perfect to 2.0 for 0%)
        multiplier = 1.0 + weakness
        raw_weights[sa.subject_id] = sa.target_pct * multiplier

    # Normalise weights and convert to integer allocations
    total_weight = sum(raw_weights.values())
    if total_weight <= 0:
        return {sa.subject_id: sa.allocated for sa in allocations}

    new_ideals: list[SubjectAllocation] = []
    for sa in allocations:
        new_sa = SubjectAllocation(
            subject_id=sa.subject_id,
            subject_code=sa.subject_code,
            target_pct=sa.target_pct,
            min_pct=sa.min_pct,
            max_pct=sa.max_pct,
            ideal=total * raw_weights[sa.subject_id] / total_weight,
            available=sa.available,
        )
        new_ideals.append(new_sa)

    _allocate_largest_remainder(new_ideals, total, rng)
    _redistribute_shortages(new_ideals, total, rng)

    return {sa.subject_id: sa.allocated for sa in new_ideals}


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _get_available_counts(
    paper: str,
    subject_ids: list[int],
    exclude_ids: set[int],
) -> dict[int, int]:
    """Return {subject_id: count} of eligible questions per subject."""
    eligible_statuses = (REVIEW_STATUS_OFFICIAL, REVIEW_STATUS_REVIEWED)

    query = (
        select(Question.subject_id, func.count(Question.id))
        .where(
            and_(
                Question.paper == paper,
                Question.subject_id.in_(subject_ids),
                Question.active == True,  # noqa: E712
                Question.review_status.in_(eligible_statuses),
                Question.id.notin_(list(exclude_ids)) if exclude_ids else True,
            )
        )
        .group_by(Question.subject_id)
        .join(
            QuestionBankImport,
            and_(
                Question.bank_import_id == QuestionBankImport.id,
                QuestionBankImport.active == True,  # noqa: E712
            ),
        )
    )
    rows = db.session.execute(query).all()
    return {row[0]: row[1] for row in rows if row[0] is not None}


def _get_cross_cutting_tag_ids() -> set[int]:
    """Return IDs of tags with is_cross_cutting=True."""
    rows = db.session.execute(
        select(Tag.id).where(Tag.is_cross_cutting == True)  # noqa: E712
    ).scalars().all()
    return set(rows)


def _fetch_questions_for_subject(
    paper: str,
    subject_id: int,
    limit: int,
    exclude_ids: set[int],
    rng: random.Random,
) -> list[tuple[int, bool]]:
    """
    Fetch `limit` eligible question IDs for a subject.
    Returns list of (question_id, has_cross_cutting_tag).
    """
    from ..models.sqe import UserSubjectStat
    from ..models.subject import question_tags, Tag as TagModel

    eligible_statuses = (REVIEW_STATUS_OFFICIAL, REVIEW_STATUS_REVIEWED)

    # Get eligible question IDs for this subject
    query = (
        select(Question.id)
        .where(
            and_(
                Question.paper == paper,
                Question.subject_id == subject_id,
                Question.active == True,  # noqa: E712
                Question.review_status.in_(eligible_statuses),
                Question.id.notin_(list(exclude_ids)) if exclude_ids else True,
            )
        )
        .join(
            QuestionBankImport,
            and_(
                Question.bank_import_id == QuestionBankImport.id,
                QuestionBankImport.active == True,  # noqa: E712
            ),
        )
    )
    all_ids = db.session.execute(query).scalars().all()

    if not all_ids:
        return []

    # Shuffle and take limit
    shuffled = list(all_ids)
    rng.shuffle(shuffled)
    chosen_ids = shuffled[:limit]

    # Check which have cross-cutting tags
    cc_tag_ids = _get_cross_cutting_tag_ids()
    if cc_tag_ids:
        cc_qs = db.session.execute(
            select(question_tags.c.question_id)
            .where(
                and_(
                    question_tags.c.question_id.in_(chosen_ids),
                    question_tags.c.tag_id.in_(list(cc_tag_ids)),
                )
            )
        ).scalars().all()
        cc_set = set(cc_qs)
    else:
        cc_set = set()

    return [(qid, qid in cc_set) for qid in chosen_ids]
