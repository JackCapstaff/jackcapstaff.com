"""
Fresh test question selection service.

Implements proportional topic allocation using the Largest Remainder Method
with seeded random tie-breaking, capacity redistribution, and final shuffle.
All functions are pure (given a seeded RNG) and independently testable.
"""
from __future__ import annotations

import random
from typing import Optional

from sqlalchemy import func

from ..extensions import db
from ..models.question import Question, QuestionBankImport
from ..models.subject import Subject


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_active_topic_counts(topic_keys: Optional[list[str]] = None) -> dict[str, int]:
    """
    Return {topic_key: eligible_question_count} for the active bank.

    If topic_keys is provided, only those topics are included.
    """
    active_bank = db.session.execute(
        db.select(QuestionBankImport).where(QuestionBankImport.active == True)  # noqa: E712
    ).scalar_one_or_none()
    if not active_bank:
        return {}

    query = (
        db.select(Question.topic_key, func.count(Question.id).label("cnt"))
        .where(
            Question.bank_import_id == active_bank.id,
            Question.active == True,  # noqa: E712
        )
        .group_by(Question.topic_key)
    )
    if topic_keys:
        query = query.where(Question.topic_key.in_(topic_keys))

    rows = db.session.execute(query).all()
    return {row.topic_key: row.cnt for row in rows}


def get_active_topics_display() -> dict[str, str]:
    """Return {topic_key: topic_display_name} for the active bank."""
    active_bank = db.session.execute(
        db.select(QuestionBankImport).where(QuestionBankImport.active == True)  # noqa: E712
    ).scalar_one_or_none()
    if not active_bank:
        return {}

    rows = db.session.execute(
        db.select(Question.topic_key, Question.topic)
        .where(
            Question.bank_import_id == active_bank.id,
            Question.active == True,  # noqa: E712
        )
        .distinct()
    ).all()
    return {row.topic_key: row.topic for row in rows}


def select_fresh_questions(
    requested: int,
    topic_keys: Optional[list[str]],
    seed: int,
    exclude_question_ids: Optional[list[int]] = None,
) -> list[Question]:
    """
    Select `requested` questions for a fresh test.

    Parameters
    ----------
    requested : int
        Total number of questions desired.
    topic_keys : list[str] or None
        Restrict to these topics; None means all topics.
    seed : int
        Random seed for reproducible selection.
    exclude_question_ids : list[int] or None
        Live question IDs to mildly deprioritise (preceding session).

    Returns
    -------
    list[Question]
        Ordered (shuffled) list of Question objects.
    """
    rng = random.Random(seed)

    active_bank = db.session.execute(
        db.select(QuestionBankImport).where(QuestionBankImport.active == True)  # noqa: E712
    ).scalar_one_or_none()
    if not active_bank:
        raise ValueError("No active question bank found.")

    topic_counts = get_active_topic_counts(topic_keys)
    if not topic_counts:
        raise ValueError("No eligible questions found for the selected topics.")

    total_available = sum(topic_counts.values())
    if requested > total_available:
        raise ValueError(
            f"Requested {requested} questions but only {total_available} are available."
        )

    quotas = largest_remainder_allocation(topic_counts, requested, rng)

    # Sample questions per topic
    selected: list[Question] = []
    for topic_key, count in quotas.items():
        if count == 0:
            continue
        q_query = (
            db.select(Question)
            .where(
                Question.bank_import_id == active_bank.id,
                Question.active == True,  # noqa: E712
                Question.topic_key == topic_key,
            )
        )
        candidates = db.session.execute(q_query).scalars().all()

        # Mild deprioritisation: sort excluded to end, then sample
        if exclude_question_ids:
            priority = [q for q in candidates if q.id not in exclude_question_ids]
            deprioritised = [q for q in candidates if q.id in exclude_question_ids]
            # Shuffle each group
            rng.shuffle(priority)
            rng.shuffle(deprioritised)
            ordered = priority + deprioritised
        else:
            ordered = list(candidates)
            rng.shuffle(ordered)

        selected.extend(ordered[:count])

    # Final shuffle with seed
    rng.shuffle(selected)
    return selected


# ---------------------------------------------------------------------------
# Largest Remainder Method
# ---------------------------------------------------------------------------


def largest_remainder_allocation(
    topic_counts: dict[str, int],
    requested: int,
    rng: random.Random,
) -> dict[str, int]:
    """
    Allocate `requested` slots across topics proportionally.

    Uses the Largest Remainder Method with seeded random tie-breaking.
    Redistributes unfilled allocations when a topic cannot supply its quota.

    Parameters
    ----------
    topic_counts : dict[str, int]
        Available question counts per topic key.
    requested : int
        Total slots to allocate.
    rng : random.Random
        Seeded RNG for tie-breaking.

    Returns
    -------
    dict[str, int]
        Allocation per topic key, summing exactly to `requested`.
    """
    available = {t: c for t, c in topic_counts.items() if c > 0}
    total_available = sum(available.values())

    if total_available < requested:
        raise ValueError(
            f"Cannot allocate {requested} from {total_available} available questions."
        )

    quotas = _initial_allocation(available, requested, rng)
    quotas = _enforce_capacity(available, quotas, requested, rng)
    return quotas


def _initial_allocation(
    available: dict[str, int], requested: int, rng: random.Random
) -> dict[str, int]:
    """Floor + largest-remainder top-up."""
    total = sum(available.values())
    ideal: dict[str, float] = {t: requested * c / total for t, c in available.items()}
    quotas: dict[str, int] = {t: int(q) for t, q in ideal.items()}
    remainders: dict[str, float] = {t: ideal[t] - quotas[t] for t in available}

    slots_left = requested - sum(quotas.values())

    # Sort by remainder DESC, random noise breaks exact ties
    sorted_topics = sorted(
        available.keys(),
        key=lambda t: (remainders[t], rng.random()),
        reverse=True,
    )
    for i in range(slots_left):
        quotas[sorted_topics[i]] += 1

    return quotas


def _enforce_capacity(
    available: dict[str, int],
    quotas: dict[str, int],
    requested: int,
    rng: random.Random,
    max_iterations: int = 100,
) -> dict[str, int]:
    """
    Iteratively cap quotas at topic capacity and redistribute overflow.
    """
    for _ in range(max_iterations):
        overflow = 0
        for t in list(quotas.keys()):
            cap = available[t]
            if quotas[t] > cap:
                overflow += quotas[t] - cap
                quotas[t] = cap

        if overflow == 0:
            break

        eligible: dict[str, int] = {
            t: available[t] - quotas[t] for t in available if available[t] > quotas[t]
        }
        if not eligible:
            raise ValueError(
                "Cannot allocate the requested number of questions; "
                "not enough questions available."
            )

        extra = _initial_allocation(eligible, overflow, rng)
        for t, e in extra.items():
            quotas[t] = quotas.get(t, 0) + e

    total = sum(quotas.values())
    if total != requested:
        raise ValueError(
            f"Allocation error: got {total} but expected {requested}."
        )
    return quotas


# ---------------------------------------------------------------------------
# Focused practice: user-chosen subjects (modules) and/or topics
# ---------------------------------------------------------------------------


def get_active_subject_counts() -> list[dict]:
    """
    Return the SQE subjects (modules) that have questions in the active bank.

    Each item: {id, code, short_name, full_name, paper, count}, ordered by
    paper then display_order.
    """
    active_bank = db.session.execute(
        db.select(QuestionBankImport).where(QuestionBankImport.active == True)  # noqa: E712
    ).scalar_one_or_none()
    if not active_bank:
        return []

    rows = db.session.execute(
        db.select(
            Subject.id,
            Subject.code,
            Subject.short_name,
            Subject.full_name,
            Subject.paper,
            func.count(Question.id).label("cnt"),
        )
        .join(Question, Question.subject_id == Subject.id)
        .where(
            Question.bank_import_id == active_bank.id,
            Question.active == True,  # noqa: E712
        )
        .group_by(
            Subject.id,
            Subject.code,
            Subject.short_name,
            Subject.full_name,
            Subject.paper,
            Subject.display_order,
        )
        .order_by(Subject.paper, Subject.display_order)
    ).all()

    return [
        {
            "id": row.id,
            "code": row.code,
            "short_name": row.short_name,
            "full_name": row.full_name,
            "paper": row.paper,
            "count": row.cnt,
        }
        for row in rows
    ]


def select_focused_questions(
    requested: int,
    subject_ids: Optional[list[int]],
    topic_keys: Optional[list[str]],
    seed: int,
) -> list[Question]:
    """
    Select questions restricted to the user-chosen subjects and/or topics.

    Questions are drawn from the active bank matching ANY selected subject
    (``Question.subject_id``) OR ANY selected topic (``Question.topic_key``).
    Slots are allocated proportionally across the chosen buckets using the
    largest-remainder method, de-duplicated, and any shortfall is topped up
    from the remaining matched pool.

    Parameters
    ----------
    requested : int
        Desired number of questions. Silently capped at the number available.
    subject_ids : list[int] or None
        SQE subject IDs to include.
    topic_keys : list[str] or None
        Legacy topic keys to include.
    seed : int
        Random seed for reproducible selection.

    Returns
    -------
    list[Question]
        Shuffled list of distinct Question objects (may be shorter than
        ``requested`` if fewer matching questions exist).
    """
    rng = random.Random(seed)

    active_bank = db.session.execute(
        db.select(QuestionBankImport).where(QuestionBankImport.active == True)  # noqa: E712
    ).scalar_one_or_none()
    if not active_bank:
        raise ValueError("No active question bank found.")

    subject_ids = [int(s) for s in (subject_ids or [])]
    topic_keys = [t for t in (topic_keys or []) if t]
    if not subject_ids and not topic_keys:
        raise ValueError("Select at least one module or topic to practise.")

    def _load(**filters) -> list[Question]:
        query = db.select(Question).where(
            Question.bank_import_id == active_bank.id,
            Question.active == True,  # noqa: E712
        )
        for attr, value in filters.items():
            query = query.where(getattr(Question, attr) == value)
        return list(db.session.execute(query).scalars().all())

    # Build buckets keyed by the chosen dimension.
    buckets: dict[tuple, list[Question]] = {}
    for sid in subject_ids:
        qs = _load(subject_id=sid)
        if qs:
            buckets[("subject", sid)] = qs
    for tk in topic_keys:
        qs = _load(topic_key=tk)
        if qs:
            buckets[("topic", tk)] = qs

    if not buckets:
        raise ValueError("No questions match your selection.")

    # Distinct pool across all buckets determines the true capacity.
    distinct: dict[int, Question] = {}
    for qs in buckets.values():
        for q in qs:
            distinct[q.id] = q

    total_available = len(distinct)
    target = min(requested, total_available)

    counts = {key: len(qs) for key, qs in buckets.items()}
    quotas = largest_remainder_allocation(counts, min(target, sum(counts.values())), rng)

    selected_ids: set[int] = set()
    selected: list[Question] = []

    # First pass: fill each bucket's quota with distinct questions.
    for key, qs in buckets.items():
        want = quotas.get(key, 0)
        if want <= 0:
            continue
        candidates = [q for q in qs if q.id not in selected_ids]
        rng.shuffle(candidates)
        for q in candidates[:want]:
            selected_ids.add(q.id)
            selected.append(q)

    # Second pass: top up any shortfall (caused by overlap between buckets).
    if len(selected) < target:
        remaining = [q for qid, q in distinct.items() if qid not in selected_ids]
        rng.shuffle(remaining)
        for q in remaining:
            if len(selected) >= target:
                break
            selected_ids.add(q.id)
            selected.append(q)

    rng.shuffle(selected)
    return selected

