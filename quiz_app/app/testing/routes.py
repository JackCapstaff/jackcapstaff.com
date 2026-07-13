"""Testing blueprint routes."""
from datetime import datetime, timezone, timedelta
import random
import json

from flask import (
    render_template,
    redirect,
    url_for,
    request,
    flash,
    jsonify,
    current_app,
)
from flask_login import login_required, current_user

from . import testing_bp
from ..extensions import db
from ..models.session import TestSession, TestSessionQuestion
from ..models.question import QuestionBankImport, Question
from ..services.question_selection import select_fresh_questions
from ..services.adaptive_selection import select_adaptive_questions


@testing_bp.route("/start", methods=["GET", "POST"])
@login_required
def start_test():
    """Start a new test session."""
    if request.method == "POST":
        test_mode = request.form.get("mode")
        time_limit = request.form.get("time_limit")
        question_count = int(request.form.get("question_count", 25))
        topics = request.form.getlist("topics")

        if test_mode not in ("fresh", "adaptive"):
            flash("Invalid test mode.", "error")
            return redirect(url_for("quiz_main.dashboard"))

        # Validate time limit
        min_limit = current_app.config["MIN_TIME_LIMIT_MINUTES"]
        max_limit = current_app.config["MAX_TIME_LIMIT_MINUTES"]

        timed = time_limit != "untimed"
        time_limit_minutes = int(time_limit) if timed else None

        if timed and (time_limit_minutes < min_limit or time_limit_minutes > max_limit):
            flash(f"Time limit must be between {min_limit} and {max_limit} minutes.", "error")
            return redirect(url_for("quiz_main.dashboard"))

        # Get active question bank
        active_bank = QuestionBankImport.query.filter_by(active=True).first()
        if not active_bank:
            flash("No active question bank. Please upload one.", "error")
            return redirect(url_for("quiz_main.dashboard"))

        # Select questions
        seed = random.randint(0, 2**31 - 1)
        
        try:
            if test_mode == "fresh":
                selected_qs = select_fresh_questions(
                    requested=question_count,
                    topic_keys=topics or None,
                    seed=seed,
                )
            else:  # adaptive
                selected_qs, _ = select_adaptive_questions(
                    user_id=current_user.id,
                    requested=question_count,
                    topic_keys=topics or None,
                    seed=seed,
                )
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("quiz_main.dashboard"))

        if not selected_qs:
            flash("Not enough questions available. Please adjust filters.", "error")
            return redirect(url_for("quiz_main.dashboard"))

        # Create test session
        session_obj = TestSession(
            user_id=current_user.id,
            mode=test_mode,
            question_count=len(selected_qs),
            selected_topics=topics if topics else None,
            timed=timed,
            time_limit_seconds=time_limit_minutes * 60 if timed else None,
            random_seed=seed,
            bank_import_id=active_bank.id,
            status="in_progress",
        )
        db.session.add(session_obj)
        db.session.flush()

        # Add questions to session
        for idx, question in enumerate(selected_qs):
            snapshot = TestSessionQuestion(
                session_id=session_obj.id,
                source_question_id=question.id,
                bank_import_id=active_bank.id,
                external_question_id=question.external_question_id,
                topic=question.topic,
                topic_key=question.topic_key,
                question_text=question.question_text,
                answer_a=question.answer_a,
                answer_b=question.answer_b,
                answer_c=question.answer_c,
                answer_d=question.answer_d,
                correct_answer=question.correct_answer,
                explanation=question.explanation,
                reference=question.reference,
                difficulty=question.difficulty,
                content_fingerprint=question.content_fingerprint,
                display_position=idx,
            )
            db.session.add(snapshot)

        db.session.commit()

        # Set start time and expiry
        session_obj.started_at = datetime.now(timezone.utc)
        if timed:
            session_obj.expires_at = session_obj.started_at + timedelta(
                seconds=session_obj.time_limit_seconds
            )
        db.session.commit()

        return redirect(url_for("quiz_testing.take_test", session_id=session_obj.id))

    # GET: Show test start form
    active_bank = QuestionBankImport.query.filter_by(active=True).first()
    topics = []
    if active_bank:
        topics = sorted(
            set(q.topic_key for q in active_bank.questions.filter_by(active=True).all())
        )

    return render_template(
        "testing/start_test.html",
        active_bank=active_bank,
        topics=topics,
        min_limit=current_app.config["MIN_TIME_LIMIT_MINUTES"],
        max_limit=current_app.config["MAX_TIME_LIMIT_MINUTES"],
    )


@testing_bp.route("/session/<int:session_id>")
@login_required
def take_test(session_id):
    """Take/view a test session."""
    session_obj = TestSession.query.get_or_404(session_id)

    if session_obj.user_id != current_user.id:
        flash("Access denied.", "error")
        return redirect(url_for("quiz_main.dashboard"))

    # Check if expired
    if session_obj.timed and session_obj.is_expired_now():
        if session_obj.status == "in_progress":
            session_obj.status = "expired"
            session_obj.submission_reason = "time_expired"
            session_obj.submitted_at = datetime.now(timezone.utc)
            _finalize_session_scores(session_obj)
            db.session.commit()
        return redirect(url_for("quiz_results.view_result", session_id=session_id))

    return render_template(
        "testing/take_test.html",
        session=session_obj,
        topic_visible=current_app.config["TOPIC_VISIBLE_DURING_TEST"],
    )


@testing_bp.route("/session/<int:session_id>/answer", methods=["POST"])
@login_required
def save_answer(session_id):
    """Save an answer via AJAX (autosave)."""
    session_obj = TestSession.query.get_or_404(session_id)

    if session_obj.user_id != current_user.id or not session_obj.is_editable:
        return jsonify({"error": "Cannot modify this session"}), 403

    data = request.get_json()
    position = data.get("position")
    answer = data.get("answer")  # A, B, C, D, or None

    if answer and answer not in ("A", "B", "C", "D"):
        return jsonify({"error": "Invalid answer"}), 400

    tsq = TestSessionQuestion.query.filter_by(
        session_id=session_id, display_position=position
    ).first()
    if not tsq:
        return jsonify({"error": "Question not found"}), 404

    if tsq.selected_answer != answer:
        tsq.answer_change_count += 1

    tsq.selected_answer = answer
    tsq.answered_at = datetime.now(timezone.utc)
    db.session.commit()

    return jsonify({"status": "saved"})


@testing_bp.route("/session/<int:session_id>/submit", methods=["POST"])
@login_required
def submit_test(session_id):
    """Submit a test session."""
    session_obj = TestSession.query.get_or_404(session_id)

    if session_obj.user_id != current_user.id or not session_obj.is_editable:
        flash("Cannot submit this session.", "error")
        return redirect(url_for("quiz_main.dashboard"))

    # Mark as submitted
    session_obj.status = "submitted"
    session_obj.submission_reason = "manual"
    session_obj.submitted_at = datetime.now(timezone.utc)

    # Score all questions
    _finalize_session_scores(session_obj)

    db.session.commit()

    flash("Test submitted successfully.", "success")
    return redirect(url_for("quiz_results.view_result", session_id=session_id))


def _finalize_session_scores(session_obj: TestSession) -> None:
    """Score all questions in a session."""
    for tsq in session_obj.questions:
        tsq.is_unanswered = tsq.selected_answer is None
        if tsq.is_unanswered:
            tsq.is_correct = False
        else:
            tsq.is_correct = tsq.selected_answer == tsq.correct_answer


