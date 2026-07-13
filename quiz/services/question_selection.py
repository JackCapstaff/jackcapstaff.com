"""
Fresh test question selection: proportional topic allocation
using the Largest Remainder Method with seeded random tie-breaking.
"""
from __future__ import annotations

import random
from typing import Optional


def get_active_topic_counts(topic_keys: Optional[list] = None) -> dict:
    from flask import current_app
    db = current_app.db
    QuestionBankImport = current_app.QuestionBankImport
    Question = current_app.Question

    active_bank = QuestionBankImport.query.filter_by(active=True).first()
    if not active_bank:
        return {}

    query = db.session.query(
        Question.topic_key,
        db.func.count(Question.id).label("cnt")
    ).filter(
        Question.bank_import_id == active_bank.id,
        Question.active == True,
    ).group_by(Question.topic_key)

    if topic_keys:
        query = query.filter(Question.topic_key.in_(topic_keys))

    return {row.topic_key: row.cnt for row in query.all()}


def get_active_topics_display() -> dict:
    """Return {topic_key: topic_display_name} for the active bank."""
    from flask import current_app
    db = current_app.db
    QuestionBankImport = current_app.QuestionBankImport
    Question = current_app.Question

    active_bank = QuestionBankImport.query.filter_by(active=True).first()
    if not active_bank:
        return {}

    rows = db.session.query(Question.topic_key, Question.topic).filter(
        Question.bank_import_id == active_bank.id,
        Question.active == True,
    ).distinct().all()
    return {r.topic_key: r.topic for r in rows}


def select_fresh_questions(
    requested: int,
    topic_keys: Optional[list],
    seed: int,
    exclude_question_ids: Optional[list] = None,
) -> list:
    """Select `requested` questions proportionally across topics."""
    from flask import current_app
    db = current_app.db
    QuestionBankImport = current_app.QuestionBankImport
    Question = current_app.Question

    rng = random.Random(seed)
    active_bank = QuestionBankImport.query.filter_by(active=True).first()
    if not active_bank:
        raise ValueError("No active question bank found.")

    topic_counts = get_active_topic_counts(topic_keys)
    if not topic_counts:
        raise ValueError("No eligible questions found for the selected topics.")

    total_available = sum(topic_counts.values())
    if requested > total_available:
        raise ValueError(f"Requested {requested} but only {total_available} available.")

    quotas = largest_remainder_allocation(topic_counts, requested, rng)

    selected = []
    excl = set(exclude_question_ids or [])
    for topic_key, count in quotas.items():
        if count == 0:
            continue
        candidates = Question.query.filter_by(
            bank_import_id=active_bank.id, active=True, topic_key=topic_key
        ).all()

        if excl:
            priority = [q for q in candidates if q.id not in excl]
            deprio = [q for q in candidates if q.id in excl]
            rng.shuffle(priority)
            rng.shuffle(deprio)
            ordered = priority + deprio
        else:
            ordered = list(candidates)
            rng.shuffle(ordered)

        selected.extend(ordered[:count])

    rng.shuffle(selected)
    return selected


def largest_remainder_allocation(
    topic_counts: dict,
    requested: int,
    rng: random.Random,
) -> dict:
    """Allocate `requested` slots proportionally with LRM and seeded tie-breaking."""
    available = {t: c for t, c in topic_counts.items() if c > 0}
    total_available = sum(available.values())

    if total_available < requested:
        raise ValueError(f"Cannot allocate {requested} from {total_available} available.")

    quotas = _initial_allocation(available, requested, rng)
    quotas = _enforce_capacity(available, quotas, requested, rng)
    return quotas


def _initial_allocation(available: dict, requested: int, rng: random.Random) -> dict:
    total = sum(available.values())
    ideal = {t: requested * c / total for t, c in available.items()}
    quotas = {t: int(q) for t, q in ideal.items()}
    remainders = {t: ideal[t] - quotas[t] for t in available}
    slots_left = requested - sum(quotas.values())
    sorted_topics = sorted(available.keys(), key=lambda t: (remainders[t], rng.random()), reverse=True)
    for i in range(slots_left):
        quotas[sorted_topics[i]] += 1
    return quotas


def _enforce_capacity(available: dict, quotas: dict, requested: int, rng: random.Random, max_iter: int = 100) -> dict:
    for _ in range(max_iter):
        overflow = 0
        for t in list(quotas.keys()):
            if quotas[t] > available[t]:
                overflow += quotas[t] - available[t]
                quotas[t] = available[t]
        if overflow == 0:
            break
        eligible = {t: available[t] - quotas[t] for t in available if available[t] > quotas[t]}
        if not eligible:
            raise ValueError("Not enough questions available for the requested count.")
        extra = _initial_allocation(eligible, overflow, rng)
        for t, e in extra.items():
            quotas[t] = quotas.get(t, 0) + e

    total = sum(quotas.values())
    if total != requested:
        raise ValueError(f"Allocation error: {total} != {requested}")
    return quotas
