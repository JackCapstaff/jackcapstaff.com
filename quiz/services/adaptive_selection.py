"""
Adaptive test selection: Bayesian topic weighting + question-level sampling.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from .question_selection import largest_remainder_allocation, get_active_topic_counts


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


def select_adaptive_questions(
    user_id: int,
    requested: int,
    topic_keys: Optional[list],
    seed: int,
    config: dict,
    exclude_question_ids: Optional[list] = None,
) -> tuple:
    from flask import current_app
    db = current_app.db
    QuestionBankImport = current_app.QuestionBankImport

    rng = random.Random(seed)
    metadata = AdaptiveMetadata(config=config)

    active_bank = QuestionBankImport.query.filter_by(active=True).first()
    if not active_bank:
        raise ValueError("No active question bank found.")

    topic_counts = get_active_topic_counts(topic_keys)
    if not topic_counts:
        raise ValueError("No eligible questions found.")

    total_available = sum(topic_counts.values())
    if requested > total_available:
        raise ValueError(f"Requested {requested} but only {total_available} available.")

    eligible_attempts = _count_user_topic_attempts(user_id, list(topic_counts.keys()), db)
    metadata.total_eligible_attempts = eligible_attempts
    min_attempts = config.get("QUIZ_MIN_ADAPTIVE_ATTEMPTS", 10)

    if eligible_attempts < min_attempts:
        metadata.cold_start_fallback = True
        from .question_selection import select_fresh_questions
        questions = select_fresh_questions(requested, topic_keys, seed, exclude_question_ids)
        return questions, metadata

    # Compute per-topic metrics
    metrics: dict = {}
    total_available_f = float(total_available)
    for topic_key, count in topic_counts.items():
        m = _compute_topic_metrics(user_id, topic_key, count / total_available_f, config, db)
        metrics[topic_key] = m

    # Normalise weights
    for m in metrics.values():
        m.raw_weight = m.baseline_share * m.weakness_multiplier
    total_w = sum(m.raw_weight for m in metrics.values()) or 1.0
    for m in metrics.values():
        m.normalised_weight = m.raw_weight / total_w

    # Virtual counts for LRM
    weighted_counts = {t: max(1, round(metrics[t].normalised_weight * 10000)) for t in topic_counts}

    max_share = config.get("QUIZ_ADAPTIVE_MAX_TOPIC_SHARE", 0.5)
    if len(weighted_counts) >= 3:
        max_virtual = round(max_share * sum(weighted_counts.values()))
        for t in weighted_counts:
            if weighted_counts[t] > max_virtual:
                weighted_counts[t] = max_virtual

    quotas = largest_remainder_allocation(weighted_counts, requested, rng)
    for t in quotas:
        quotas[t] = min(quotas[t], topic_counts[t])

    # Ensure at least 1 per eligible topic
    if requested >= len(topic_counts):
        for t in topic_counts:
            if quotas.get(t, 0) == 0 and topic_counts[t] > 0:
                quotas[t] = 1

    _rebalance(quotas, topic_counts, requested, rng)

    # Question-level sampling
    preceding_ids = set(exclude_question_ids or [])
    recent_cutoff = datetime.utcnow() - timedelta(days=7)
    all_selected = []

    for topic_key, count in quotas.items():
        if count == 0:
            continue
        candidates = _get_topic_candidates(active_bank.id, topic_key)
        if not candidates:
            continue
        q_hist = _question_history(user_id, topic_key, db)
        selected = _weighted_sample(candidates, count, q_hist, preceding_ids, recent_cutoff, rng)
        all_selected.extend(selected)

    rng.shuffle(all_selected)
    metadata.topic_metrics = {
        tk: {
            "baseline_share": round(m.baseline_share, 4),
            "combined_accuracy": round(m.combined_accuracy, 4),
            "weakness_multiplier": round(m.weakness_multiplier, 4),
            "normalised_weight": round(m.normalised_weight, 4),
            "quota": quotas.get(tk, 0),
        }
        for tk, m in metrics.items()
    }
    return all_selected, metadata


def _compute_topic_metrics(user_id, topic_key, baseline_share, config, db):
    from flask import current_app
    TestSession = current_app.TestSession
    TestSessionQuestion = current_app.TestSessionQuestion

    m = TopicMetrics(topic_key=topic_key, baseline_share=baseline_share)

    lt = db.session.query(
        db.func.count(TestSessionQuestion.id).label("attempts"),
        db.func.sum(db.cast(TestSessionQuestion.is_correct == True, db.Integer)).label("correct"),
    ).join(TestSession, TestSessionQuestion.session_id == TestSession.id).filter(
        TestSession.user_id == user_id,
        TestSession.status.in_(["submitted", "expired"]),
        TestSessionQuestion.topic_key == topic_key,
        TestSessionQuestion.is_correct.isnot(None),
    ).one()
    m.lifetime_attempts = lt.attempts or 0
    m.lifetime_correct = int(lt.correct or 0)

    limit = config.get("QUIZ_ADAPTIVE_RECENT_ATTEMPTS", 30)
    recent_ids = db.session.query(TestSessionQuestion.id).join(
        TestSession, TestSessionQuestion.session_id == TestSession.id
    ).filter(
        TestSession.user_id == user_id,
        TestSession.status.in_(["submitted", "expired"]),
        TestSessionQuestion.topic_key == topic_key,
        TestSessionQuestion.is_correct.isnot(None),
    ).order_by(TestSession.submitted_at.desc()).limit(limit).all()
    recent_ids = [r[0] for r in recent_ids]

    if recent_ids:
        rc = db.session.query(
            db.func.count(TestSessionQuestion.id).label("attempts"),
            db.func.sum(db.cast(TestSessionQuestion.is_correct == True, db.Integer)).label("correct"),
        ).filter(TestSessionQuestion.id.in_(recent_ids)).one()
        m.recent_attempts = rc.attempts or 0
        m.recent_correct = int(rc.correct or 0)

    prior_mean, prior_strength = 0.5, 4
    m.smoothed_lifetime_accuracy = (m.lifetime_correct + prior_mean * prior_strength) / (m.lifetime_attempts + prior_strength)
    m.smoothed_recent_accuracy = (m.recent_correct + prior_mean * prior_strength) / (m.recent_attempts + prior_strength)

    if m.recent_attempts > 0:
        m.combined_accuracy = 0.65 * m.smoothed_recent_accuracy + 0.35 * m.smoothed_lifetime_accuracy
    else:
        m.combined_accuracy = m.smoothed_lifetime_accuracy

    strength = config.get("QUIZ_ADAPTIVE_STRENGTH", 1.5)
    m.weakness_multiplier = 1 + strength * (1 - m.combined_accuracy)
    return m


def _count_user_topic_attempts(user_id, topic_keys, db):
    from flask import current_app
    TestSession = current_app.TestSession
    TestSessionQuestion = current_app.TestSessionQuestion

    row = db.session.query(db.func.count(TestSessionQuestion.id)).join(
        TestSession, TestSessionQuestion.session_id == TestSession.id
    ).filter(
        TestSession.user_id == user_id,
        TestSession.status.in_(["submitted", "expired"]),
        TestSessionQuestion.topic_key.in_(topic_keys),
        TestSessionQuestion.is_correct.isnot(None),
    ).scalar()
    return row or 0


def _get_topic_candidates(bank_import_id, topic_key):
    from flask import current_app
    Question = current_app.Question
    return Question.query.filter_by(bank_import_id=bank_import_id, active=True, topic_key=topic_key).all()


def _question_history(user_id, topic_key, db):
    from flask import current_app
    TestSession = current_app.TestSession
    TestSessionQuestion = current_app.TestSessionQuestion

    rows = db.session.query(
        TestSessionQuestion.external_question_id,
        TestSessionQuestion.content_fingerprint,
        TestSessionQuestion.is_correct,
        TestSession.submitted_at,
    ).join(TestSession, TestSessionQuestion.session_id == TestSession.id).filter(
        TestSession.user_id == user_id,
        TestSession.status.in_(["submitted", "expired"]),
        TestSessionQuestion.topic_key == topic_key,
        TestSessionQuestion.is_correct.isnot(None),
    ).order_by(TestSession.submitted_at.asc()).all()

    history: dict = {}
    for r in rows:
        eid = r.external_question_id
        if eid not in history:
            history[eid] = {"attempts": 0, "incorrect": 0, "consecutive_correct": 0, "last_dt": None, "fingerprint": r.content_fingerprint}
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


def _weighted_sample(candidates, count, q_hist, preceding_ids, recent_cutoff, rng, w_min=0.10, w_max=5.0):
    weights = []
    for q in candidates:
        h = q_hist.get(q.external_question_id)
        w = 1.0
        if h is None:
            w *= 1.25
        elif h.get("fingerprint") == q.content_fingerprint:
            if h["consecutive_correct"] == 0 and h["attempts"] > 0:
                w *= 2.0
            if h.get("incorrect", 0) > 0:
                w *= 1 + min(2.0, 0.5 * h["incorrect"])
            if h.get("consecutive_correct", 0) >= 3:
                w *= 0.60
            last_dt = h.get("last_dt")
            if last_dt and last_dt >= recent_cutoff:
                w *= 0.60
        else:
            w *= 1.25

        if q.id in preceding_ids:
            w *= 0.25

        weights.append(max(w_min, min(w_max, w)))

    selected = []
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


def _rebalance(quotas, available, requested, rng):
    current = sum(quotas.values())
    diff = requested - current
    if diff == 0:
        return
    if diff > 0:
        eligible = {t: available[t] - quotas[t] for t in quotas if available[t] > quotas[t]}
        for t in sorted(eligible.keys(), key=lambda _: rng.random()):
            if diff == 0:
                break
            give = min(diff, eligible[t])
            quotas[t] += give
            diff -= give
    else:
        for t in sorted(quotas.keys(), key=lambda t: -quotas[t]):
            if diff == 0:
                break
            take = min(-diff, quotas[t])
            quotas[t] -= take
            diff += take
