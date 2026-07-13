"""Reporting and analytics queries."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta


def get_topic_breakdown(session):
    """Return per-topic breakdown dict for a completed session."""
    topics: dict = {}
    for tsq in session.questions:
        tk = tsq.topic_key
        if tk not in topics:
            topics[tk] = {
                "topic": tsq.topic,
                "total": 0, "correct": 0, "incorrect": 0, "unanswered": 0,
            }
        t = topics[tk]
        t["total"] += 1
        if tsq.is_unanswered:
            t["unanswered"] += 1
        elif tsq.is_correct:
            t["correct"] += 1
        else:
            t["incorrect"] += 1

    for t in topics.values():
        t["percentage"] = (t["correct"] / t["total"] * 100) if t["total"] else 0.0

    return topics


def get_user_performance(user_id: int) -> dict:
    """Return aggregate performance stats for a user."""
    from flask import current_app
    db = current_app.db
    TestSession = current_app.TestSession
    TestSessionQuestion = current_app.TestSessionQuestion
    QuestionBankImport = current_app.QuestionBankImport

    sessions = TestSession.query.filter(
        TestSession.user_id == user_id,
        TestSession.status.in_(["submitted", "expired"]),
    ).order_by(TestSession.submitted_at.asc()).all()

    total_sessions = len(sessions)
    total_attempts = 0
    total_correct = 0
    unique_questions: set = set()
    topic_stats: dict = defaultdict(lambda: {"attempts": 0, "correct": 0})
    scores = []

    for sess in sessions:
        for tsq in sess.questions:
            if tsq.is_correct is not None:
                total_attempts += 1
                unique_questions.add((tsq.external_question_id, tsq.content_fingerprint))
                if tsq.is_correct:
                    total_correct += 1
                topic_stats[tsq.topic_key]["attempts"] += 1
                topic_stats[tsq.topic_key]["correct"] += tsq.is_correct
                topic_stats[tsq.topic_key]["topic"] = tsq.topic
        scores.append(sess.percentage())

    # Topic accuracy
    topic_accuracy = []
    for tk, stats in topic_stats.items():
        acc = stats["correct"] / stats["attempts"] * 100 if stats["attempts"] else 0
        topic_accuracy.append({
            "topic_key": tk,
            "topic": stats.get("topic", tk),
            "attempts": stats["attempts"],
            "correct": stats["correct"],
            "accuracy": round(acc, 1),
        })
    topic_accuracy.sort(key=lambda x: x["accuracy"])

    # Score trend (last 20)
    score_trend = [{"session_num": i + 1, "score": round(s, 1)} for i, s in enumerate(scores[-20:])]

    # Active bank coverage
    active_bank = QuestionBankImport.query.filter_by(active=True).first()
    bank_q_count = 0
    if active_bank:
        from flask import current_app
        Question = current_app.Question
        bank_q_count = Question.query.filter_by(bank_import_id=active_bank.id, active=True).count()

    # Repeatedly incorrect questions
    repeated_incorrect = db.session.query(
        TestSessionQuestion.external_question_id,
        TestSessionQuestion.topic,
        db.func.count(TestSessionQuestion.id).label("cnt"),
    ).join(TestSession, TestSessionQuestion.session_id == TestSession.id).filter(
        TestSession.user_id == user_id,
        TestSession.status.in_(["submitted", "expired"]),
        TestSessionQuestion.is_correct == False,
        TestSessionQuestion.is_unanswered == False,
    ).group_by(
        TestSessionQuestion.external_question_id, TestSessionQuestion.topic
    ).having(db.func.count(TestSessionQuestion.id) > 1).order_by(
        db.func.count(TestSessionQuestion.id).desc()
    ).limit(20).all()

    return {
        "completed_sessions": total_sessions,
        "total_attempts": total_attempts,
        "unique_questions": len(unique_questions),
        "lifetime_accuracy": round(total_correct / total_attempts * 100, 1) if total_attempts else 0,
        "average_score": round(sum(scores) / len(scores), 1) if scores else 0,
        "topic_accuracy": topic_accuracy,
        "strongest_topics": topic_accuracy[-3:][::-1] if len(topic_accuracy) >= 3 else [],
        "weakest_topics": topic_accuracy[:3],
        "score_trend": score_trend,
        "bank_question_count": bank_q_count,
        "repeated_incorrect": [
            {"external_id": r.external_question_id, "topic": r.topic, "count": r.cnt}
            for r in repeated_incorrect
        ],
    }


def get_retest_eligibility(source_session_id: int, include_unanswered: bool = False) -> dict:
    """
    Check how many incorrect/unanswered questions from a session
    still exist unchanged in the active bank.
    """
    from flask import current_app
    TestSessionQuestion = current_app.TestSessionQuestion
    QuestionBankImport = current_app.QuestionBankImport
    Question = current_app.Question

    if include_unanswered:
        candidates = TestSessionQuestion.query.filter(
            TestSessionQuestion.session_id == source_session_id,
            (TestSessionQuestion.is_correct == False) | (TestSessionQuestion.is_unanswered == True),
        ).all()
    else:
        candidates = TestSessionQuestion.query.filter_by(
            session_id=source_session_id, is_correct=False, is_unanswered=False
        ).all()

    active_bank = QuestionBankImport.query.filter_by(active=True).first()
    if not active_bank:
        return {"eligible": 0, "unavailable": len(candidates), "total": len(candidates)}

    eligible = 0
    unavailable = 0
    for sq in candidates:
        live = Question.query.filter_by(
            bank_import_id=active_bank.id,
            external_question_id=sq.external_question_id,
            content_fingerprint=sq.content_fingerprint,
            active=True,
        ).first()
        if live:
            eligible += 1
        else:
            unavailable += 1

    return {"eligible": eligible, "unavailable": unavailable, "total": len(candidates)}
