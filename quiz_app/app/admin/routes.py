"""Admin blueprint routes."""
import io
from datetime import datetime, timezone, timedelta

from flask import (
    render_template,
    redirect,
    url_for,
    request,
    flash,
    current_app,
    send_file,
)
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from . import admin_bp
from ..extensions import db
from ..models.question import QuestionBankImport, Question, StagedImport, StagedQuestion
from ..models.user import User
from ..services.csv_import import stage_import, confirm_import


def _admin_required(f):
    """Decorator to ensure user is admin."""
    from functools import wraps

    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin:
            flash("Admin access required.", "error")
            return redirect(url_for("quiz_main.dashboard"))
        return f(*args, **kwargs)

    return decorated_function


@admin_bp.route("/")
@_admin_required
def index():
    """Admin dashboard."""
    active_bank = QuestionBankImport.query.filter_by(active=True).first()
    all_banks = QuestionBankImport.query.order_by(QuestionBankImport.imported_at.desc()).all()
    user_count = User.query.count()

    return render_template(
        "admin/index.html",
        active_bank=active_bank,
        all_banks=all_banks,
        user_count=user_count,
    )


@admin_bp.route("/upload", methods=["GET", "POST"])
@_admin_required
def upload_questions():
    """Upload and preview question bank."""
    if request.method == "POST":
        if "file" not in request.files:
            flash("No file part.", "error")
            return redirect(url_for("quiz_admin.upload_questions"))

        file = request.files["file"]
        if file.filename == "":
            flash("No file selected.", "error")
            return redirect(url_for("quiz_admin.upload_questions"))

        # Save temporary file
        filename = secure_filename(file.filename)
        file_data = file.read()

        # Parse and validate CSV
        result = stage_import(file_data, filename, current_user.id)

        if result.has_errors:
            for err in result.errors:
                flash(f"Row {err.row}, {err.field}: {err.message}", "error")
            return redirect(url_for("quiz_admin.upload_questions"))

        staged = StagedImport.query.filter_by(token=result.token).first()
        if not staged:
            flash("Import staging failed.", "error")
            return redirect(url_for("quiz_admin.upload_questions"))

        return redirect(url_for("quiz_admin.preview_import", token=staged.token))

    return render_template("admin/upload.html")


@admin_bp.route("/preview/<token>")
@_admin_required
def preview_import(token):
    """Preview staged import before confirmation."""
    staged = StagedImport.query.filter_by(token=token).first_or_404()

    if staged.user_id != current_user.id:
        flash("Access denied.", "error")
        return redirect(url_for("quiz_admin.index"))

    if staged.status != "pending":
        flash("This import is no longer available.", "error")
        return redirect(url_for("quiz_admin.index"))

    if staged.expires_at < datetime.now(timezone.utc):
        staged.status = "expired"
        db.session.commit()
        flash("This import has expired.", "error")
        return redirect(url_for("quiz_admin.index"))

    active_bank = QuestionBankImport.query.filter_by(active=True).first()

    return render_template(
        "admin/preview.html",
        staged=staged,
        active_bank=active_bank,
    )


@admin_bp.route("/confirm/<token>", methods=["POST"])
@_admin_required
def confirm_import_route(token):
    """Confirm and activate the staged import."""
    try:
        new_bank = confirm_import(token, current_user.id)
        flash(
            f"Question bank activated: {new_bank.question_count} questions from {new_bank.topic_count} topics.",
            "success",
        )
        return redirect(url_for("quiz_admin.index"))
    except ValueError as e:
        flash(f"Cannot confirm import: {str(e)}", "error")
        return redirect(url_for("quiz_admin.index"))


@admin_bp.route("/export")
@_admin_required
def export_current_bank():
    """Export the current active question bank."""
    active_bank = QuestionBankImport.query.filter_by(active=True).first()
    if not active_bank:
        flash("No active question bank.", "error")
        return redirect(url_for("quiz_admin.index"))

    # Generate CSV
    lines = [
        "Question ID,Topic,Question,Answer A,Answer B,Answer C,Answer D,Correct Answer,Explanation,Difficulty,Active,Image URL,Reference,Last Updated"
    ]

    for q in active_bank.questions:
        explanation = (q.explanation or "").replace(",", ";").replace("\n", " ")
        reference = (q.reference or "").replace(",", ";").replace("\n", " ")
        lines.append(
            f'"{q.external_question_id}","{q.topic}","{q.question_text.replace('"', '""')}","{q.answer_a.replace('"', '""')}","{q.answer_b.replace('"', '""')}","{q.answer_c.replace('"', '""')}","{q.answer_d.replace('"', '""')}","{q.correct_answer}","{explanation}","{q.difficulty or ""}","{q.active}","{q.image_url or ""}","{reference}","{q.last_updated or ""}"'
        )

    csv_data = "\n".join(lines).encode("utf-8")
    return send_file(
        io.BytesIO(csv_data),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"questions_export_{active_bank.id}.csv",
    )


@admin_bp.route("/users")
@_admin_required
def manage_users():
    """Manage user accounts."""
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template("admin/users.html", users=users)


@admin_bp.route("/users/<int:user_id>/toggle-active", methods=["POST"])
@_admin_required
def toggle_user_active(user_id):
    """Toggle user active status."""
    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash("Cannot deactivate yourself.", "error")
        return redirect(url_for("quiz_admin.manage_users"))

    user.active = not user.active
    db.session.commit()

    status = "activated" if user.active else "deactivated"
    flash(f"User {user.username} has been {status}.", "success")
    return redirect(url_for("quiz_admin.manage_users"))


