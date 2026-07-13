"""Main blueprint routes."""
from flask import render_template, redirect, url_for
from flask_login import login_required, current_user
from . import main_bp
from ..models.session import TestSession
from ..models.question import QuestionBankImport


@main_bp.route("/")
def index():
    """Home page."""
    if current_user.is_authenticated:
        return redirect(url_for("quiz_main.dashboard"))
    return render_template("index.html")


@main_bp.route("/dashboard")
@login_required
def dashboard():
    """User dashboard with test options."""
    # Get active question bank
    active_bank = QuestionBankImport.query.filter_by(active=True).first()

    # Get user's recent sessions
    recent_sessions = (
        TestSession.query.filter_by(user_id=current_user.id)
        .order_by(TestSession.created_at.desc())
        .limit(5)
        .all()
    )

    # Get performance summary
    completed_sessions = TestSession.query.filter_by(
        user_id=current_user.id, status="submitted"
    ).all()

    avg_score = 0.0
    if completed_sessions:
        avg_score = sum(s.percentage() for s in completed_sessions) / len(completed_sessions)

    return render_template(
        "main/dashboard.html",
        active_bank=active_bank,
        recent_sessions=recent_sessions,
        avg_score=avg_score,
        total_completed=len(completed_sessions),
    )


