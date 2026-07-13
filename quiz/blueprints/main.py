"""Quiz main blueprint: dashboard and new test setup."""
from flask import (
    Blueprint, current_app, flash, redirect, render_template, request, session, url_for
)
from ..auth_utils import get_current_quiz_user, quiz_login_required
from ..services.question_selection import get_active_topic_counts, get_active_topics_display
from ..services.test_sessions import create_test_session
from ..services.reporting import get_user_performance

main_bp = Blueprint("quiz_main", __name__, template_folder="../../templates/quiz/main")


@main_bp.before_request
def inject_csrf():
    """Generate a CSRF token for the session if absent."""
    import secrets
    if "quiz_csrf_token" not in session:
        session["quiz_csrf_token"] = secrets.token_hex(32)


@main_bp.context_processor
def inject_quiz_globals():
    return {
        "quiz_user": get_current_quiz_user(),
        "quiz_csrf_token": session.get("quiz_csrf_token", ""),
    }


@main_bp.route("/")
@quiz_login_required
def index():
    user = get_current_quiz_user()
    perf = get_user_performance(user.id)
    topic_counts = get_active_topic_counts()
    return render_template("quiz/main/index.html", user=user, perf=perf,
                           topic_counts=topic_counts,
                           has_bank=bool(topic_counts))


@main_bp.route("/new-test", methods=["GET", "POST"])
@quiz_login_required
def new_test():
    user = get_current_quiz_user()
    topics_display = get_active_topics_display()
    topic_counts = get_active_topic_counts()
    cfg = current_app.config

    if request.method == "POST":
        from ..blueprints.auth import _check_csrf
        _check_csrf()

        mode = request.form.get("mode", "fresh")
        if mode not in ("fresh", "adaptive"):
            flash("Invalid test mode.", "danger")
            return redirect(url_for("quiz_main.new_test"))

        all_topics = request.form.get("all_topics") == "1"
        if all_topics:
            selected_keys = None
        else:
            selected_keys = request.form.getlist("topics") or None

        try:
            requested = int(request.form.get("question_count", 20))
        except (ValueError, TypeError):
            flash("Invalid question count.", "danger")
            return redirect(url_for("quiz_main.new_test"))

        timed = request.form.get("timed") == "1"
        time_limit = None
        if timed:
            try:
                time_limit = int(request.form.get("time_limit_minutes", 30))
            except (ValueError, TypeError):
                flash("Invalid time limit.", "danger")
                return redirect(url_for("quiz_main.new_test"))

        try:
            sess = create_test_session(
                user_id=user.id,
                mode=mode,
                topic_keys=selected_keys,
                requested=requested,
                timed=timed,
                time_limit_minutes=time_limit,
                all_topics=all_topics,
            )
            return redirect(url_for("quiz_testing.question", session_id=sess.id, position=0))
        except ValueError as e:
            flash(str(e), "danger")

    return render_template(
        "quiz/main/new_test.html",
        user=user,
        topics_display=topics_display,
        topic_counts=topic_counts,
        min_time=cfg.get("QUIZ_MIN_TIME_LIMIT_MINUTES", 1),
        max_time=cfg.get("QUIZ_MAX_TIME_LIMIT_MINUTES", 480),
        has_bank=bool(topic_counts),
    )
