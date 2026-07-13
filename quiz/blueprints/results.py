"""Quiz results blueprint: results, review, history, performance."""
from flask import (
    Blueprint, abort, current_app, redirect, render_template, request, session, url_for
)
from ..auth_utils import get_current_quiz_user, quiz_login_required
from ..services.reporting import get_topic_breakdown, get_user_performance, get_retest_eligibility
from ..services.test_sessions import create_retest_session

results_bp = Blueprint("quiz_results", __name__, template_folder="../../templates/quiz/results")


@results_bp.context_processor
def inject_quiz_globals():
    return {
        "quiz_user": get_current_quiz_user(),
        "quiz_csrf_token": session.get("quiz_csrf_token", ""),
    }


@results_bp.route("/<int:session_id>")
@quiz_login_required
def results(session_id):
    user = get_current_quiz_user()
    sess = _get_complete_session(session_id, user.id)
    topic_breakdown = get_topic_breakdown(sess)
    sorted_topics = sorted(topic_breakdown.values(), key=lambda t: -t["percentage"])
    retest_info = get_retest_eligibility(session_id)

    return render_template(
        "quiz/results/results.html",
        sess=sess,
        topic_breakdown=topic_breakdown,
        sorted_topics=sorted_topics,
        strongest=sorted_topics[:3],
        weakest=sorted_topics[-3:][::-1] if len(sorted_topics) >= 3 else sorted_topics,
        retest_info=retest_info,
        user=user,
    )


@results_bp.route("/<int:session_id>/review")
@quiz_login_required
def review(session_id):
    user = get_current_quiz_user()
    sess = _get_complete_session(session_id, user.id)

    filter_mode = request.args.get("filter", "all")
    topic_filter = request.args.get("topic", "")
    qid_filter = request.args.get("qid", "").strip()

    questions = sess.questions
    if filter_mode == "incorrect":
        questions = [q for q in questions if q.is_correct is False and not q.is_unanswered]
    elif filter_mode == "unanswered":
        questions = [q for q in questions if q.is_unanswered]
    elif filter_mode == "correct":
        questions = [q for q in questions if q.is_correct is True]

    if topic_filter:
        questions = [q for q in questions if q.topic_key == topic_filter]
    if qid_filter:
        questions = [q for q in questions if qid_filter.lower() in q.external_question_id.lower()]

    all_topics = {q.topic_key: q.topic for q in sess.questions}

    return render_template(
        "quiz/results/review.html",
        sess=sess,
        questions=questions,
        filter_mode=filter_mode,
        topic_filter=topic_filter,
        qid_filter=qid_filter,
        all_topics=all_topics,
        user=user,
    )


@results_bp.route("/<int:session_id>/retest", methods=["POST"])
@quiz_login_required
def retest(session_id):
    user = get_current_quiz_user()
    sess = _get_complete_session(session_id, user.id)
    _check_csrf()

    include_unanswered = request.form.get("include_unanswered") == "1"
    timed = request.form.get("timed") == "1"
    time_limit = None
    if timed:
        try:
            time_limit = int(request.form.get("time_limit_minutes", 30))
        except (ValueError, TypeError):
            pass

    try:
        new_sess, unavailable = create_retest_session(
            user.id, session_id, include_unanswered, timed, time_limit
        )
        return redirect(url_for("quiz_testing.question", session_id=new_sess.id, position=0))
    except ValueError as e:
        from flask import flash
        flash(str(e), "danger")
        return redirect(url_for("quiz_results.results", session_id=session_id))


@results_bp.route("/history")
@quiz_login_required
def history():
    user = get_current_quiz_user()
    TestSession = current_app.TestSession

    sort_by = request.args.get("sort", "date")
    filter_mode = request.args.get("mode", "")
    filter_timed = request.args.get("timed", "")

    page = request.args.get("page", 1, type=int)
    per_page = 20

    query = TestSession.query.filter(
        TestSession.user_id == user.id,
        TestSession.status.in_(["submitted", "expired"]),
    )
    if filter_mode:
        query = query.filter(TestSession.mode == filter_mode)
    if filter_timed == "1":
        query = query.filter(TestSession.timed == True)
    elif filter_timed == "0":
        query = query.filter(TestSession.timed == False)

    if sort_by == "score":
        # We must load and sort in memory for computed score
        all_sessions = query.order_by(TestSession.submitted_at.desc()).all()
        all_sessions.sort(key=lambda s: s.percentage(), reverse=True)
        total = len(all_sessions)
        sessions = all_sessions[(page - 1) * per_page: page * per_page]
    else:
        total = query.count()
        sessions = query.order_by(TestSession.submitted_at.desc()).offset((page - 1) * per_page).limit(per_page).all()

    total_pages = (total + per_page - 1) // per_page

    return render_template(
        "quiz/results/history.html",
        sessions=sessions,
        user=user,
        page=page,
        total_pages=total_pages,
        sort_by=sort_by,
        filter_mode=filter_mode,
        filter_timed=filter_timed,
    )


@results_bp.route("/performance")
@quiz_login_required
def performance():
    user = get_current_quiz_user()
    perf = get_user_performance(user.id)
    return render_template("quiz/results/performance.html", user=user, perf=perf)


def _get_complete_session(session_id, user_id):
    TestSession = current_app.TestSession
    sess = TestSession.query.filter_by(id=session_id, user_id=user_id).first()
    if not sess:
        abort(404)
    if not sess.is_complete:
        abort(403)
    return sess


def _check_csrf():
    token = session.get("quiz_csrf_token")
    form_token = request.form.get("csrf_token")
    if not token or token != form_token:
        abort(403)
