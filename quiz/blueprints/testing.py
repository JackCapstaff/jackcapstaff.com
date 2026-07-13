"""Quiz test-taking blueprint: question display, autosave, submit."""
from datetime import datetime
from flask import (
    Blueprint, abort, current_app, flash, jsonify, redirect,
    render_template, request, session, url_for
)
from ..auth_utils import get_current_quiz_user, quiz_login_required
from ..services.test_sessions import autosave_answer, pause_session, resume_session, check_and_finalize_expired
from ..services.scoring import score_session, get_unanswered_count

testing_bp = Blueprint("quiz_testing", __name__, template_folder="../../templates/quiz/testing")


@testing_bp.context_processor
def inject_quiz_globals():
    from ..blueprints.main import inject_quiz_globals as _main_ctx
    return {
        "quiz_user": get_current_quiz_user(),
        "quiz_csrf_token": session.get("quiz_csrf_token", ""),
    }


@testing_bp.route("/<int:session_id>/q/<int:position>")
@quiz_login_required
def question(session_id, position):
    user = get_current_quiz_user()
    sess = _get_editable_session(session_id, user.id)

    # Check expiry on every load
    if check_and_finalize_expired(session_id, user.id):
        flash("Time expired — your test has been submitted.", "warning")
        return redirect(url_for("quiz_results.results", session_id=session_id))

    questions = sess.questions
    total = len(questions)

    if position < 0 or position >= total:
        position = 0

    sess.current_position = position
    current_app.db.session.commit()

    tsq = questions[position]
    topic_visible = current_app.config.get("QUIZ_TOPIC_VISIBLE_DURING_TEST", True)

    answered_positions = {q.display_position for q in questions if q.selected_answer}

    return render_template(
        "quiz/testing/question.html",
        sess=sess,
        tsq=tsq,
        position=position,
        total=total,
        questions=questions,
        answered_positions=answered_positions,
        topic_visible=topic_visible,
        now_ts=int(datetime.utcnow().timestamp()),
    )


@testing_bp.route("/<int:session_id>/save", methods=["POST"])
@quiz_login_required
def save_answer(session_id):
    """Autosave endpoint. Never returns correctness."""
    user = get_current_quiz_user()
    _check_csrf()

    position = request.form.get("position", type=int)
    answer = request.form.get("answer", "").upper().strip()

    if position is None:
        return jsonify({"ok": False, "error": "Missing position."}), 400

    ok, err = autosave_answer(session_id, user.id, position, answer)
    if not ok:
        return jsonify({"ok": False, "error": err}), 400
    return jsonify({"ok": True})


@testing_bp.route("/<int:session_id>/pause", methods=["POST"])
@quiz_login_required
def pause(session_id):
    user = get_current_quiz_user()
    _check_csrf()
    try:
        pause_session(session_id, user.id)
        flash("Test paused.", "info")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("quiz_main.index"))


@testing_bp.route("/<int:session_id>/resume", methods=["POST"])
@quiz_login_required
def resume(session_id):
    user = get_current_quiz_user()
    _check_csrf()
    try:
        resume_session(session_id, user.id)
    except ValueError as e:
        flash(str(e), "danger")
        return redirect(url_for("quiz_main.index"))

    sess = current_app.TestSession.query.filter_by(id=session_id, user_id=user.id).first()
    pos = sess.current_position if sess else 0
    return redirect(url_for("quiz_testing.question", session_id=session_id, position=pos))


@testing_bp.route("/<int:session_id>/submit", methods=["GET", "POST"])
@quiz_login_required
def submit(session_id):
    user = get_current_quiz_user()
    sess = _get_editable_session(session_id, user.id)

    if check_and_finalize_expired(session_id, user.id):
        return redirect(url_for("quiz_results.results", session_id=session_id))

    if request.method == "POST":
        _check_csrf()
        db = current_app.db
        TestSessionQuestion = current_app.TestSessionQuestion
        score_session(sess, submission_reason="manual", db=db, TestSessionQuestion=TestSessionQuestion)
        return redirect(url_for("quiz_results.results", session_id=session_id))

    unanswered = get_unanswered_count(session_id)
    return render_template("quiz/testing/submit_confirm.html", sess=sess, unanswered=unanswered)


@testing_bp.route("/<int:session_id>/expire", methods=["POST"])
@quiz_login_required
def expire_now(session_id):
    """Called by the browser when the countdown reaches zero."""
    user = get_current_quiz_user()
    _check_csrf()
    check_and_finalize_expired(session_id, user.id)
    return jsonify({"ok": True, "redirect": url_for("quiz_results.results", session_id=session_id)})


def _get_editable_session(session_id, user_id):
    TestSession = current_app.TestSession
    sess = TestSession.query.filter_by(id=session_id, user_id=user_id).first()
    if not sess:
        abort(404)
    if not sess.is_editable:
        if sess.is_complete:
            from flask import redirect as redir
            from flask import url_for as uf
            return redir(uf("quiz_results.results", session_id=session_id))
        abort(403)
    return sess


def _check_csrf():
    token = session.get("quiz_csrf_token")
    form_token = request.form.get("csrf_token") or request.json and request.json.get("csrf_token")
    if not token or token != form_token:
        abort(403)
