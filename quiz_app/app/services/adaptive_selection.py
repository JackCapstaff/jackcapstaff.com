"""
Adaptive test selection service.

Stages
------
1. Compute per-topic performance metrics for the user.
2. Calculate weighted topic allocation (Bayesian smoothing + weakness multiplier).
3. Within each topic, score each candidate question and sample without replacement.

All computation functions are pure and independently testable.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from flask import current_app
from sqlalchemy import func

from ..extensions import db
from ..models.question import Question, QuestionBankImport
from ..models.session import TestSession, TestSessionQuestion
from .question_selection import (
    largest_remainder_allocation,
    get_active_topic_counts,
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TopicMetrics:
    topic_key: str
    baseline_share: float = 0.0
    lifetime_attempts: int = 0
    lifetime_correct: int = 0
    recent_attempts: int = 0
    recent_correct: int = 0
    smoothed_lifetime_accuracy: float = 0.5
    smoothed_recent_accuracy: float = 0.5
    combined_accuracy: float = 0.5
    weakness_multiplier: float = 1.0
    raw_weight: float = 0.0
    normalised_weight: float = 0.0


@dataclass
class AdaptiveMetadata:
    cold_start_fallback: bool = False
    total_eligible_attempts: int = 0
    topic_metrics: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def select_adaptive_questions(
    user_id: int,
    requested: int,
    topic_keys: Optional[list[str]],
    seed: int,
    exclude_question_ids: Optional[list[int]] = None,
) -> tuple[list[Question], AdaptiveMetadata]:
    """
    Select questions for an adaptive test.

    Returns (ordered question list, AdaptiveMetadata) where metadata
    captures the computation for debugging / auditing.
    """
    rng = random.Random(seed)
    cfg = _load_config()
    metadata = AdaptiveMetadata(config=cfg)

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
            f"Requested {requested} but only {total_available} available."
        )

    # Count total eligible attempts for cold-start check
    eligible_attempts = _count_user_topic_attempts(
        user_id, list(topic_counts.keys())
    )
    metadata.total_eligible_attempts = eligible_attempts

    if eligible_attempts < cfg["min_adaptive_attempts"]:
        # Cold-start fallback: use fresh selection
        metadata.cold_start_fallback = True
        from .question_selection import select_fresh_questions
        questions = select_fresh_questions(requested, topic_keys, seed, exclude_question_ids)
        return questions, metadata

    # --- Compute topic metrics ---
    metrics: dict[str, TopicMetrics] = {}
    total_available_f = float(total_available)
    for topic_key, count in topic_counts.items():
        m = _compute_topic_metrics(
            user_id=user_id,
            topic_key=topic_key,
            baseline_share=count / total_available_f,
            cfg=cfg,
        )
        metrics[topic_key] = m

    # --- Compute raw weights and normalise ---
    for m in metrics.values():
        m.raw_weight = m.baseline_share * m.weakness_multiplier
    total_weight = sum(m.raw_weight for m in metrics.values()) or 1.0
    for m in metrics.values():
        m.normalised_weight = m.raw_weight / total_weight

    # --- Build weighted topic counts for allocation ---
    weighted_counts: dict[str, int] = {}
    for topic_key, count in topic_counts.items():
        m = metrics[topic_key]
        # Scale available count by normalised weight relative to baseline
        # so largest_remainder_allocation sees "virtual" counts
        weighted_counts[topic_key] = max(1, round(m.normalised_weight * 10000))

    # Clamp max topic share
    max_share = cfg["adaptive_max_topic_share"]
    num_topics = len(weighted_counts)
    if num_topics >= 3:
        max_virtual = round(max_share * sum(weighted_counts.values()))
        for t in weighted_counts:
            if weighted_counts[t] > max_virtual and topic_counts[t] < requested:
                weighted_counts[t] = max_virtual

    quotas = largest_remainder_allocation(weighted_counts, requested, rng)
    # Cap by actual availability
    for t in quotas:
        quotas[t] = min(quotas[t], topic_counts[t])

    # Ensure at least one per eligible topic when request allows
    if requested >= len(topic_counts):
        for t in topic_counts:
            if quotas.get(t, 0) == 0 and topic_counts[t] > 0:
                quotas[t] = 1

    # Re-balance sum (capacity may have been reduced)
    _rebalance_quotas(quotas, topic_counts, requested, rng)

    # --- Question-level weighted selection within each topic ---
    preceding_session_q_ids = set(exclude_question_ids or [])
    recent_cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    all_selected: list[Question] = []
    for topic_key, count in quotas.items():
        if count == 0:
            continue
        candidates = _get_topic_candidates(
            active_bank.id, topic_key, list(preceding_session_q_ids)
        )
        if not candidates:
            continue
        q_hist = _question_history_for_topic(user_id, topic_key)
        selected = _weighted_sample(candidates, count, q_hist, preceding_session_q_ids, recent_cutoff, rng)
        all_selected.extend(selected)

    rng.shuffle(all_selected)

    metadata.topic_metrics = {
        tk: {
            "baseline_share": round(m.baseline_share, 4),
            "smoothed_lifetime_accuracy": round(m.smoothed_lifetime_accuracy, 4),
            "smoothed_recent_accuracy": round(m.smoothed_recent_accuracy, 4),
            "combined_accuracy": round(m.combined_accuracy, 4),
            "weakness_multiplier": round(m.weakness_multiplier, 4),
            "normalised_weight": round(m.normalised_weight, 4),
            "quota": quotas.get(tk, 0),
        }
        for tk, m in metrics.items()
    }

    return all_selected, metadata


# ---------------------------------------------------------------------------
# Topic metrics helpers
# ---------------------------------------------------------------------------


def _compute_topic_metrics(
    user_id: int, topic_key: str, baseline_share: float, cfg: dict
) -> TopicMetrics:
    m = TopicMetrics(topic_key=topic_key, baseline_share=baseline_share)

    # Lifetime
    lifetime_rows = db.session.execute(
        db.select(
            func.count(TestSessionQuestion.id).label("attempts"),
            func.sum(
                db.cast(TestSessionQuestion.is_correct == True, db.Integer)  # noqa: E712
            ).label("correct"),
        )
        .join(TestSession, TestSessionQuestion.session_id == TestSession.id)
        .where(
            TestSession.user_id == user_id,
            TestSession.status.in_(["submitted", "expired"]),
            TestSessionQuestion.topic_key == topic_key,
            TestSessionQuestion.is_correct.is_not(None),
        )
    ).one()
    m.lifetime_attempts = lifetime_rows.attempts or 0
    m.lifetime_correct = int(lifetime_rows.correct or 0)

    # Recent
    limit = cfg["adaptive_recent_attempts"]
    recent_ids = db.session.execute(
        db.select(TestSessionQuestion.id)
        .join(TestSession, TestSessionQuestion.session_id == TestSession.id)
        .where(
            TestSession.user_id == user_id,
            TestSession.status.in_(["submitted", "expired"]),
            TestSessionQuestion.topic_key == topic_key,
            TestSessionQuestion.is_correct.is_not(None),
        )
        .order_by(TestSession.submitted_at.desc())
        .limit(limit)
    ).scalars().all()

    if recent_ids:
        recent_rows = db.session.execute(
            db.select(
                func.count(TestSessionQuestion.id).label("attempts"),
                func.sum(
                    db.cast(TestSessionQuestion.is_correct == True, db.Integer)  # noqa: E712
                ).label("correct"),
            ).where(TestSessionQuestion.id.in_(recent_ids))
        ).one()
        m.recent_attempts = recent_rows.attempts or 0
        m.recent_correct = int(recent_rows.correct or 0)

    # Bayesian smoothing
    prior_mean, prior_strength = 0.5, 4
    m.smoothed_lifetime_accuracy = (m.lifetime_correct + prior_mean * prior_strength) / (
        m.lifetime_attempts + prior_strength
    )
    m.smoothed_recent_accuracy = (m.recent_correct + prior_mean * prior_strength) / (
        m.recent_attempts + prior_strength
    )

    if m.recent_attempts > 0:
        m.combined_accuracy = (
            0.65 * m.smoothed_recent_accuracy + 0.35 * m.smoothed_lifetime_accuracy
        )
    else:
        m.combined_accuracy = m.smoothed_lifetime_accuracy

    strength = cfg["adaptive_strength"]
    m.weakness_multiplier = 1 + strength * (1 - m.combined_accuracy)
    return m


def _count_user_topic_attempts(user_id: int, topic_keys: list[str]) -> int:
    row = db.session.execute(
        db.select(func.count(TestSessionQuestion.id))
        .join(TestSession, TestSessionQuestion.session_id == TestSession.id)
        .where(
            TestSession.user_id == user_id,
            TestSession.status.in_(["submitted", "expired"]),
            TestSessionQuestion.topic_key.in_(topic_keys),
            TestSessionQuestion.is_correct.is_not(None),
        )
    ).scalar()
    return row or 0


# ---------------------------------------------------------------------------
# Question-level weighting
# ---------------------------------------------------------------------------


def _get_topic_candidates(
    bank_import_id: int, topic_key: str, exclude_ids: list[int]
) -> list[Question]:
    return db.session.execute(
        db.select(Question).where(
            Question.bank_import_id == bank_import_id,
            Question.active == True,  # noqa: E712
            Question.topic_key == topic_key,
        )
    ).scalars().all()


def _question_history_for_topic(user_id: int, topic_key: str) -> dict[str, dict]:
    """
    Return per-external-question-id history.
    Keys: external_question_id
    Values: {attempts, incorrect, last_correct_streak, last_attempt_dt, last_fingerprint}
    """
    rows = db.session.execute(
        db.select(
            TestSessionQuestion.external_question_id,
            TestSessionQuestion.content_fingerprint,
            TestSessionQuestion.is_correct,
            TestSession.submitted_at,
        )
        .join(TestSession, TestSessionQuestion.session_id == TestSession.id)
        .where(
            TestSession.user_id == user_id,
            TestSession.status.in_(["submitted", "expired"]),
            TestSessionQuestion.topic_key == topic_key,
            TestSessionQuestion.is_correct.is_not(None),
        )
        .order_by(TestSession.submitted_at.asc())
    ).all()

    history: dict[str, dict] = {}
    for r in rows:
        eid = r.external_question_id
        if eid not in history:
            history[eid] = {
                "attempts": 0,
                "incorrect": 0,
                "consecutive_correct": 0,
                "last_dt": None,
                "fingerprint": r.content_fingerprint,
            }
        h = history[eid]
        h["attempts"] += 1
        h["fingerprint"] = r.content_fingerprint
        if r.is_correct:
            h["consecutive_correct"] += 1
        else:
            h["incorrect"] += 1
            h["consecutive_correct"] = 0
        h["last_dt"] = r.submitted_at
    return history


def _weighted_sample(
    candidates: list[Question],
    count: int,
    q_hist: dict,
    preceding_ids: set[int],
    recent_cutoff: datetime,
    rng: random.Random,
    weight_min: float = 0.10,
    weight_max: float = 5.00,
) -> list[Question]:
    """Seeded weighted sample without replacement."""
    weights: list[float] = []
    for q in candidates:
        h = q_hist.get(q.external_question_id)
        w = 1.0

        if h is None:
            # Never attempted
            w *= 1.25
        else:
            # Check fingerprint match (same content)
            if h.get("fingerprint") == q.content_fingerprint:
                last_incorrect = h.get("incorrect", 0) - (
                    1 if h.get("consecutive_correct", 0) == 0 else 0
                )
                # Last attempt was incorrect
                if h["consecutive_correct"] == 0 and h["attempts"] > 0:
                    w *= 2.0
                # Repeated incorrect
                prior_incorrect = h.get("incorrect", 0)
                if prior_incorrect > 0:
                    w *= 1 + min(2.0, 0.5 * prior_incorrect)
                # Mastered (correct 3+ times consecutively)
                if h.get("consecutive_correct", 0) >= 3:
                    w *= 0.60
                # Attempted within past 7 days
                last_dt = h.get("last_dt")
                if last_dt and (
                    last_dt.replace(tzinfo=timezone.utc) if last_dt.tzinfo is None else last_dt
                ) >= recent_cutoff:
                    w *= 0.60
            else:
                # Content changed — treat as new
                w *= 1.25

        # Appeared in preceding session
        if q.id in preceding_ids:
            w *= 0.25

        # Clamp
        w = max(weight_min, min(weight_max, w))
        weights.append(w)

    # Weighted sample without replacement
    selected: list[Question] = []
    available = list(zip(candidates, weights))
    for _ in range(min(count, len(available))):
        total_w = sum(ww for _, ww in available)
        pick = rng.random() * total_w
        cumulative = 0.0
        chosen_idx = 0
        for idx, (_, ww) in enumerate(available):
            cumulative += ww
            if pick <= cumulative:
                chosen_idx = idx
                break
        selected.append(available[chosen_idx][0])
        available.pop(chosen_idx)

    return selected


def _rebalance_quotas(
    quotas: dict[str, int],
    available: dict[str, int],
    requested: int,
    rng: random.Random,
) -> None:
    """Ensure quotas sum exactly to requested after capacity capping."""
    current = sum(quotas.values())
    diff = requested - current
    if diff == 0:
        return

    if diff > 0:
        eligible = {t: available[t] - quotas[t] for t in quotas if available[t] > quotas[t]}
        keys = sorted(eligible.keys(), key=lambda _: rng.random())
        for t in keys:
            if diff == 0:
                break
            give = min(diff, eligible[t])
            quotas[t] += give
            diff -= give
    else:
        # diff < 0 (over-allocated): reduce from topics with most slack
        keys = sorted(quotas.keys(), key=lambda t: -quotas[t])
        for t in keys:
            if diff == 0:
                break
            take = min(-diff, quotas[t])
            quotas[t] -= take
            diff += take


def _load_config() -> dict:
    """Read adaptive settings from Flask config."""
    from flask import current_app
    return {
        "min_adaptive_attempts": current_app.config.get("MIN_ADAPTIVE_ATTEMPTS", 10),
        "adaptive_recent_attempts": current_app.config.get("ADAPTIVE_RECENT_ATTEMPTS", 30),
        "adaptive_strength": current_app.config.get("ADAPTIVE_STRENGTH", 1.5),
        "adaptive_max_topic_share": current_app.config.get("ADAPTIVE_MAX_TOPIC_SHARE", 0.5),
    }
