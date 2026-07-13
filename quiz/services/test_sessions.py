"""
Test session creation, autosave, pause/resume, and expiry finalization.
"""
from __future__ import annotations

import random
import secrets
from datetime import datetime, timedelta
from typing import Optional


def _get_config():
    from flask import current_app
    cfg = current_app.config
    return {
        "QUIZ_MIN_TIME_LIMIT_MINUTES": cfg.get("QUIZ_MIN_TIME_LIMIT_MINUTES", 1),
        "QUIZ_MAX_TIME_LIMIT_MINUTES": cfg.get("QUIZ_MAX_TIME_LIMIT_MINUTES", 480),
        "QUIZ_MIN_ADAPTIVE_ATTEMPTS": cfg.get("QUIZ_MIN_ADAPTIVE_ATTEMPTS", 10),
        "QUIZ_ADAPTIVE_RECENT_ATTEMPTS": cfg.get("QUIZ_ADAPTIVE_RECENT_ATTEMPTS", 30),
        "QUIZ_ADAPTIVE_STRENGTH": cfg.get("QUIZ_ADAPTIVE_STRENGTH", 1.5),
        "QUIZ_ADAPTIVE_MAX_TOPIC_SHARE": cfg.get("QUIZ_ADAPTIVE_MAX_TOPIC_SHARE", 0.5),
        "QUIZ_TOPIC_VISIBLE": cfg.get("QUIZ_TOPIC_VISIBLE_DURING_TEST", True),
    }


def create_test_session(
    user_id: int,
    mode: str,
    topic_keys: Optional[list],
    requested: int,
    timed: bool,
    time_limit_minutes: Optional[int],
    all_topics: bool = False,
):
    """
    Create a TestSession and its TestSessionQuestion snapshots in one transaction.

    Returns the created TestSession.
    Raises ValueError with a user-friendly message on validation failure.
    """
    from flask import current_app
    from .question_selection import select_fresh_questions, get_active_topic_counts
    from .adaptive_selection import select_adaptive_questions

    db = current_app.db
    TestSession = current_app.TestSession
    TestSessionQuestion = current_app.TestSessionQuestion
    QuestionBankImport = current_app.QuestionBankImport
    cfg = _get_config()

    # Validate time limit
    if timed:
        if time_limit_minutes is None:
            raise ValueError("Time limit is required for a timed test.")
        if time_limit_minutes < cfg["QUIZ_MIN_TIME_LIMIT_MINUTES"]:
            raise ValueError(f"Minimum time limit is {cfg['QUIZ_MIN_TIME_LIMIT_MINUTES']} minute(s).")
        if time_limit_minutes > cfg["QUIZ_MAX_TIME_LIMIT_MINUTES"]:
            raise ValueError(f"Maximum time limit is {cfg['QUIZ_MAX_TIME_LIMIT_MINUTES']} minutes.")

    active_bank = QuestionBankImport.query.filter_by(active=True).first()
    if not active_bank:
        raise ValueError("No active question bank is available.")

    effective_topics = None if all_topics else (topic_keys or None)
    topic_counts = get_active_topic_counts(effective_topics)

    if not topic_counts:
        raise ValueError("No eligible questions for the selected topics.")
    total_available = sum(topic_counts.values())
    if requested < 1:
        raise ValueError("Question count must be at least 1.")
    if requested > total_available:
        raise ValueError(f"Only {total_available} questions available for the selected topics.")

    seed = random.randint(0, 2**31 - 1)

    # Find preceding session questions for mild deprioritisation
    prev_q_ids = _get_preceding_session_question_ids(user_id, db)

    if mode == "fresh":
        questions = select_fresh_questions(requested, effective_topics, seed, prev_q_ids)
        adaptive_metadata = None
        cold_start = False
    elif mode == "adaptive":
        questions, meta = select_adaptive_questions(
            user_id, requested, effective_topics, seed, cfg, prev_q_ids
        )
        adaptive_metadata = meta.topic_metrics
        cold_start = meta.cold_start_fallback
    else:
        raise ValueError(f"Unknown test mode: {mode!r}")

    if len(questions) != requested:
        raise ValueError("Could not select the requested number of questions.")

    now = datetime.utcnow()
    expires_at = (now + timedelta(minutes=time_limit_minutes)) if timed else None

    session = TestSession(
        user_id=user_id,
        mode=mode,
        status="in_progress",
        question_count=requested,
        selected_topics=effective_topics,
        timed=timed,
        time_limit_seconds=(time_limit_minutes * 60) if timed else None,
        started_at=now,
        expires_at=expires_at,
        random_seed=seed,
        current_position=0,
        bank_import_id=active_bank.id,
        adaptive_cold_start=cold_start,
        adaptive_metadata=adaptive_metadata,
    )
    db.session.add(session)
    db.session.flush()

    for pos, q in enumerate(questions):
        tsq = TestSessionQuestion(
            session_id=session.id,
            source_question_id=q.id,
            bank_import_id=q.bank_import_id,
            external_question_id=q.external_question_id,
            content_fingerprint=q.content_fingerprint,
            topic=q.topic,
            topic_key=q.topic_key,
            question_text=q.question_text,
            answer_a=q.answer_a,
            answer_b=q.answer_b,
            answer_c=q.answer_c,
            answer_d=q.answer_d,
            correct_answer=q.correct_answer,
            explanation=q.explanation,
            reference=q.reference,
            difficulty=q.difficulty,
            display_position=pos,
            selected_answer=None,
            is_correct=None,
            is_unanswered=False,
        )
        db.session.add(tsq)

    db.session.commit()
    return session


def create_retest_session(
    user_id: int,
    source_session_id: int,
    include_unanswered: bool = False,
    timed: bool = False,
    time_limit_minutes: Optional[int] = None,
):
    """Create a retest session from incorrect/unanswered questions in a completed session."""
    from flask import current_app
    from .question_selection import get_active_topic_counts

    db = current_app.db
    TestSession = current_app.TestSession
    TestSessionQuestion = current_app.TestSessionQuestion
    QuestionBankImport = current_app.QuestionBankImport
    Question = current_app.Question

    source = TestSession.query.filter_by(id=source_session_id, user_id=user_id).first()
    if not source:
        raise ValueError("Source test session not found.")
    if not source.is_complete:
        raise ValueError("Source session is not yet completed.")

    cfg = _get_config()
    if timed and time_limit_minutes:
        if time_limit_minutes < cfg["QUIZ_MIN_TIME_LIMIT_MINUTES"]:
            raise ValueError(f"Minimum time limit is {cfg['QUIZ_MIN_TIME_LIMIT_MINUTES']} minute(s).")
        if time_limit_minutes > cfg["QUIZ_MAX_TIME_LIMIT_MINUTES"]:
            raise ValueError(f"Maximum time limit is {cfg['QUIZ_MAX_TIME_LIMIT_MINUTES']} minutes.")

    # Find questions to retest
    filters = [TestSessionQuestion.is_correct == False]
    if include_unanswered:
        filters = [
            (TestSessionQuestion.is_correct == False) | (TestSessionQuestion.is_unanswered == True)
        ]

    source_q_filter = TestSessionQuestion.session_id == source_session_id
    if include_unanswered:
        candidates_from_source = TestSessionQuestion.query.filter(
            source_q_filter,
            (TestSessionQuestion.is_correct == False) | (TestSessionQuestion.is_unanswered == True),
        ).all()
    else:
        candidates_from_source = TestSessionQuestion.query.filter(
            source_q_filter,
            TestSessionQuestion.is_correct == False,
            TestSessionQuestion.is_unanswered == False,
        ).all()

    active_bank = QuestionBankImport.query.filter_by(active=True).first()
    if not active_bank:
        raise ValueError("No active question bank found.")

    # Match source questions to active bank by ext ID + fingerprint
    eligible_live = []
    unavailable_count = 0
    for sq in candidates_from_source:
        live_q = Question.query.filter_by(
            bank_import_id=active_bank.id,
            external_question_id=sq.external_question_id,
            content_fingerprint=sq.content_fingerprint,
            active=True,
        ).first()
        if live_q:
            eligible_live.append(live_q)
        else:
            unavailable_count += 1

    if not eligible_live:
        raise ValueError(
            f"None of the {len(candidates_from_source)} incorrect questions exist in the current question bank."
        )

    seed = random.randint(0, 2**31 - 1)
    rng = random.Random(seed)
    rng.shuffle(eligible_live)

    now = datetime.utcnow()
    expires_at = (now + timedelta(minutes=time_limit_minutes)) if timed and time_limit_minutes else None

    session = TestSession(
        user_id=user_id,
        mode="retest",
        status="in_progress",
        question_count=len(eligible_live),
        selected_topics=None,
        timed=timed,
        time_limit_seconds=(time_limit_minutes * 60) if timed and time_limit_minutes else None,
        started_at=now,
        expires_at=expires_at,
        random_seed=seed,
        current_position=0,
        source_session_id=source_session_id,
        bank_import_id=active_bank.id,
    )
    db.session.add(session)
    db.session.flush()

    for pos, q in enumerate(eligible_live):
        tsq = TestSessionQuestion(
            session_id=session.id,
            source_question_id=q.id,
            bank_import_id=q.bank_import_id,
            external_question_id=q.external_question_id,
            content_fingerprint=q.content_fingerprint,
            topic=q.topic,
            topic_key=q.topic_key,
            question_text=q.question_text,
            answer_a=q.answer_a,
            answer_b=q.answer_b,
            answer_c=q.answer_c,
            answer_d=q.answer_d,
            correct_answer=q.correct_answer,
            explanation=q.explanation,
            reference=q.reference,
            difficulty=q.difficulty,
            display_position=pos,
            selected_answer=None,
            is_correct=None,
            is_unanswered=False,
        )
        db.session.add(tsq)

    db.session.commit()
    return session, unavailable_count


def autosave_answer(session_id: int, user_id: int, position: int, answer: str):
    """
    Save a selected answer for one question. Returns (success, error_message).
    Never returns correctness information.
    """
    from flask import current_app
    db = current_app.db
    TestSession = current_app.TestSession
    TestSessionQuestion = current_app.TestSessionQuestion

    if answer not in ("A", "B", "C", "D"):
        return False, "Invalid answer."

    sess = TestSession.query.filter_by(id=session_id, user_id=user_id).first()
    if not sess:
        return False, "Session not found."
    if not sess.is_editable:
        return False, "Session is not editable."
    if sess.is_expired_now():
        _finalize_expired(sess, db, TestSessionQuestion)
        return False, "Test time has expired."

    tsq = TestSessionQuestion.query.filter_by(session_id=session_id, display_position=position).first()
    if not tsq:
        return False, "Question not found."

    if tsq.selected_answer is not None and tsq.selected_answer != answer:
        tsq.answer_change_count += 1

    tsq.selected_answer = answer
    tsq.answered_at = datetime.utcnow()
    sess.current_position = position
    db.session.commit()
    return True, None


def pause_session(session_id: int, user_id: int):
    from flask import current_app
    db = current_app.db
    TestSession = current_app.TestSession

    sess = TestSession.query.filter_by(id=session_id, user_id=user_id).first()
    if not sess:
        raise ValueError("Session not found.")
    if sess.timed:
        raise ValueError("Timed tests cannot be paused.")
    if sess.status != "in_progress":
        raise ValueError("Session is not in progress.")
    sess.status = "paused"
    sess.paused_at = datetime.utcnow()
    db.session.commit()


def resume_session(session_id: int, user_id: int):
    from flask import current_app
    db = current_app.db
    TestSession = current_app.TestSession

    sess = TestSession.query.filter_by(id=session_id, user_id=user_id).first()
    if not sess:
        raise ValueError("Session not found.")
    if sess.status != "paused":
        raise ValueError("Session is not paused.")
    sess.status = "in_progress"
    sess.paused_at = None
    db.session.commit()


def check_and_finalize_expired(session_id: int, user_id: int):
    """Check if a session has expired and finalize it if so. Returns True if expired."""
    from flask import current_app
    db = current_app.db
    TestSession = current_app.TestSession
    TestSessionQuestion = current_app.TestSessionQuestion

    sess = TestSession.query.filter_by(id=session_id, user_id=user_id).first()
    if not sess or not sess.is_editable:
        return False
    if sess.is_expired_now():
        _finalize_expired(sess, db, TestSessionQuestion)
        return True
    return False


def _finalize_expired(sess, db, TestSessionQuestion):
    """Finalize an expired session with scoring."""
    from .scoring import score_session
    if sess.status in ("submitted", "expired"):
        return
    score_session(sess, submission_reason="time_expired", db=db, TestSessionQuestion=TestSessionQuestion)


def _get_preceding_session_question_ids(user_id: int, db) -> list:
    """Return live question IDs from the user's last completed session."""
    from flask import current_app
    TestSession = current_app.TestSession
    TestSessionQuestion = current_app.TestSessionQuestion

    last_session = TestSession.query.filter(
        TestSession.user_id == user_id,
        TestSession.status.in_(["submitted", "expired"]),
    ).order_by(TestSession.submitted_at.desc()).first()

    if not last_session:
        return []

    ids = [
        tsq.source_question_id
        for tsq in last_session.questions
        if tsq.source_question_id is not None
    ]
    return ids
