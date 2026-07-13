"""Results blueprint routes."""
from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from sqlalchemy import desc

from . import results_bp
from ..models.session import TestSession, TestSessionQuestion
from ..models.question import QuestionBankImport


@results_bp.route("/session/<int:session_id>")
@login_required
def view_result(session_id):
    """View results for a completed test."""
    session_obj = TestSession.query.get_or_404(session_id)

    if session_obj.user_id != current_user.id:
        flash("Access denied.", "error")
        return redirect(url_for("main.dashboard"))

    if not session_obj.is_complete:
        flash("This test is not yet submitted.", "info")
        return redirect(url_for("testing.take_test", session_id=session_id))

    # Compute performance by topic
    topic_stats = {}
    for tsq in session_obj.questions:
        if tsq.topic_key not in topic_stats:
            topic_stats[tsq.topic_key] = {"topic": tsq.topic, "correct": 0, "total": 0}
        topic_stats[tsq.topic_key]["total"] += 1
        if tsq.is_correct:
            topic_stats[tsq.topic_key]["correct"] += 1

    return render_template(
        "results/result.html",
        session=session_obj,
        topic_stats=topic_stats,
    )


@results_bp.route("/session/<int:session_id>/review")
@login_required
def review_test(session_id):
    """Detailed review of test performance."""
    session_obj = TestSession.query.get_or_404(session_id)

    if session_obj.user_id != current_user.id:
        flash("Access denied.", "error")
        return redirect(url_for("main.dashboard"))

    if not session_obj.is_complete:
        flash("This test is not yet submitted.", "info")
        return redirect(url_for("testing.take_test", session_id=session_id))

    # Filter for incorrect and unanswered questions
    incorrect = [
        q for q in session_obj.questions if q.is_correct is False or q.is_unanswered
    ]

    return render_template(
        "results/review.html",
        session=session_obj,
        incorrect_questions=incorrect,
    )


@results_bp.route("/history")
@login_required
def history():
    """View user's test history."""
    page = request.args.get("page", 1, type=int)
    per_page = 10

    sessions = (
        TestSession.query.filter_by(user_id=current_user.id)
        .order_by(desc(TestSession.created_at))
        .paginate(page=page, per_page=per_page)
    )

    return render_template("results/history.html", sessions=sessions)


@results_bp.route("/analytics")
@login_required
def analytics():
    """User performance analytics."""
    completed_sessions = (
        TestSession.query.filter_by(user_id=current_user.id, status="submitted")
        .order_by(desc(TestSession.created_at))
        .all()
    )

    if not completed_sessions:
        return render_template("results/analytics.html", stats=None)

    # Aggregate stats
    total_tests = len(completed_sessions)
    total_questions = sum(s.question_count for s in completed_sessions)
    total_correct = sum(s.correct_count() for s in completed_sessions)
    avg_score = (total_correct / total_questions * 100) if total_questions else 0

    # Topic performance
    topic_perf = {}
    for session in completed_sessions:
        for tsq in session.questions:
            if tsq.topic_key not in topic_perf:
                topic_perf[tsq.topic_key] = {"topic": tsq.topic, "correct": 0, "total": 0}
            topic_perf[tsq.topic_key]["total"] += 1
            if tsq.is_correct:
                topic_perf[tsq.topic_key]["correct"] += 1

    # Mode performance
    mode_perf = {}
    for session in completed_sessions:
        if session.mode not in mode_perf:
            mode_perf[session.mode] = {"count": 0, "correct": 0, "total": 0}
        mode_perf[session.mode]["count"] += 1
        mode_perf[session.mode]["total"] += session.question_count
        mode_perf[session.mode]["correct"] += session.correct_count()

    stats = {
        "total_tests": total_tests,
        "total_questions": total_questions,
        "total_correct": total_correct,
        "avg_score": avg_score,
        "topic_perf": topic_perf,
        "mode_perf": mode_perf,
    }

    return render_template("results/analytics.html", stats=stats)
