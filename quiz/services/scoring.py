"""Scoring and submission service."""
from __future__ import annotations

from datetime import datetime


def score_session(sess, submission_reason: str = "manual", db=None, TestSessionQuestion=None):
    """
    Score all questions in a session and finalize it.
    Idempotent: if already submitted/expired, returns immediately.
    """
    if sess.status in ("submitted", "expired"):
        return

    if db is None:
        from flask import current_app
        db = current_app.db
    if TestSessionQuestion is None:
        from flask import current_app
        TestSessionQuestion = current_app.TestSessionQuestion

    now = datetime.utcnow()
    status = "expired" if submission_reason == "time_expired" else "submitted"

    for tsq in sess.questions:
        if tsq.selected_answer is None:
            tsq.is_correct = False
            tsq.is_unanswered = True
        else:
            tsq.is_correct = tsq.selected_answer == tsq.correct_answer
            tsq.is_unanswered = False

    sess.status = status
    sess.submitted_at = now
    sess.submission_reason = submission_reason
    db.session.commit()


def get_unanswered_count(session_id: int) -> int:
    from flask import current_app
    TestSessionQuestion = current_app.TestSessionQuestion
    return TestSessionQuestion.query.filter_by(
        session_id=session_id, selected_answer=None
    ).count()
