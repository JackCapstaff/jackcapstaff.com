"""Quiz admin blueprint: question bank management, user management."""
import io
from flask import (
    Blueprint, abort, current_app, flash, make_response, redirect,
    render_template, request, session, url_for
)
from ..auth_utils import get_current_quiz_user, quiz_admin_required
from ..services.csv_import import stage_import, confirm_import, cancel_staged_import, get_active_bank_as_csv
from ..services.question_selection import get_active_topic_counts, get_active_topics_display

quiz_admin_bp = Blueprint("quiz_admin", __name__, template_folder="../../templates/quiz/admin")


@quiz_admin_bp.context_processor
def inject_quiz_globals():
    return {
        "quiz_user": get_current_quiz_user(),
        "quiz_csrf_token": session.get("quiz_csrf_token", ""),
    }


@quiz_admin_bp.route("/")
@quiz_admin_required
def index():
    user = get_current_quiz_user()
    QuestionBankImport = current_app.QuestionBankImport
    active_bank = QuestionBankImport.query.filter_by(active=True).first()
    topic_counts = get_active_topic_counts()
    recent_imports = QuestionBankImport.query.order_by(
        QuestionBankImport.imported_at.desc()
    ).limit(10).all()

    return render_template(
        "quiz/admin/index.html",
        user=user,
        active_bank=active_bank,
        topic_counts=topic_counts,
        recent_imports=recent_imports,
        total_questions=sum(topic_counts.values()),
    )


@quiz_admin_bp.route("/upload", methods=["GET", "POST"])
@quiz_admin_required
def upload():
    user = get_current_quiz_user()

    if request.method == "POST":
        _check_csrf()
        file = request.files.get("question_file")
        if not file or not file.filename:
            flash("Please select a file to upload.", "danger")
            return redirect(url_for("quiz_admin.upload"))

        content = file.read()
        max_size = current_app.config.get("MAX_CONTENT_LENGTH", 10 * 1024 * 1024)
        if len(content) > max_size:
            flash("File is too large.", "danger")
            return redirect(url_for("quiz_admin.upload"))

        result = stage_import(content, file.filename, user.id, current_app.config)
        if result.has_errors:
            return render_template("quiz/admin/upload_errors.html", user=user, result=result)

        return redirect(url_for("quiz_admin.preview", token=result.token))

    return render_template("quiz/admin/upload.html", user=user)


@quiz_admin_bp.route("/preview/<token>")
@quiz_admin_required
def preview(token):
    user = get_current_quiz_user()
    StagedImport = current_app.StagedImport
    staged = StagedImport.query.filter_by(token=token, user_id=user.id, status="pending").first()
    if not staged:
        flash("Preview not found or has expired.", "warning")
        return redirect(url_for("quiz_admin.upload"))

    from datetime import datetime
    if datetime.utcnow() > staged.expires_at:
        flash("Staged import has expired. Please re-upload.", "warning")
        return redirect(url_for("quiz_admin.upload"))

    active_bank = current_app.QuestionBankImport.query.filter_by(active=True).first()
    return render_template(
        "quiz/admin/preview.html",
        user=user,
        staged=staged,
        active_bank=active_bank,
    )


@quiz_admin_bp.route("/confirm/<token>", methods=["POST"])
@quiz_admin_required
def confirm(token):
    user = get_current_quiz_user()
    _check_csrf()

    try:
        bank = confirm_import(token, user.id, current_app.config)
        flash(f"Successfully imported {bank.question_count} questions across {bank.topic_count} topics.", "success")
    except ValueError as e:
        flash(str(e), "danger")

    return redirect(url_for("quiz_admin.index"))


@quiz_admin_bp.route("/cancel/<token>", methods=["POST"])
@quiz_admin_required
def cancel(token):
    user = get_current_quiz_user()
    _check_csrf()
    cancel_staged_import(token, user.id)
    flash("Import cancelled.", "info")
    return redirect(url_for("quiz_admin.index"))


@quiz_admin_bp.route("/export")
@quiz_admin_required
def export_bank():
    csv_content = get_active_bank_as_csv()
    if not csv_content:
        flash("No active question bank to export.", "warning")
        return redirect(url_for("quiz_admin.index"))

    response = make_response(csv_content)
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = "attachment; filename=quiz_questions.csv"
    return response


@quiz_admin_bp.route("/questions")
@quiz_admin_required
def questions():
    user = get_current_quiz_user()
    QuestionBankImport = current_app.QuestionBankImport
    Question = current_app.Question

    active_bank = QuestionBankImport.query.filter_by(active=True).first()
    if not active_bank:
        return render_template("quiz/admin/questions.html", user=user, questions=[], page=1, total_pages=1, active_bank=None)

    topic_filter = request.args.get("topic", "")
    search = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = 30

    query = Question.query.filter_by(bank_import_id=active_bank.id)
    if topic_filter:
        query = query.filter_by(topic_key=topic_filter)
    if search:
        query = query.filter(
            Question.question_text.ilike(f"%{search}%") |
            Question.external_question_id.ilike(f"%{search}%")
        )

    total = query.count()
    qs = query.order_by(Question.topic_key, Question.external_question_id).offset(
        (page - 1) * per_page
    ).limit(per_page).all()

    return render_template(
        "quiz/admin/questions.html",
        user=user,
        questions=qs,
        active_bank=active_bank,
        topic_filter=topic_filter,
        search=search,
        page=page,
        total_pages=(total + per_page - 1) // per_page,
        topics_display=get_active_topics_display(),
    )


@quiz_admin_bp.route("/users")
@quiz_admin_required
def users():
    user = get_current_quiz_user()
    QuizUser = current_app.QuizUser
    all_users = QuizUser.query.order_by(QuizUser.created_at.desc()).all()
    return render_template("quiz/admin/users.html", user=user, all_users=all_users)


@quiz_admin_bp.route("/users/<int:uid>/toggle", methods=["POST"])
@quiz_admin_required
def toggle_user(uid):
    _check_csrf()
    db = current_app.db
    QuizUser = current_app.QuizUser
    target = QuizUser.query.get_or_404(uid)
    target.active = not target.active
    db.session.commit()
    state = "activated" if target.active else "deactivated"
    flash(f"User '{target.username}' {state}.", "success")
    return redirect(url_for("quiz_admin.users"))


def _check_csrf():
    token = session.get("quiz_csrf_token")
    form_token = request.form.get("csrf_token")
    if not token or token != form_token:
        abort(403)
