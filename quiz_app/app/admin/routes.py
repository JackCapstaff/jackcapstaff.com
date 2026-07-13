"""Admin blueprint routes."""
import io
import csv
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
from ..extensions import db, User
from ..models.question import (
    QuestionBankImport,
    Question,
    StagedImport,
    StagedQuestion,
    FORMAT_SQE5,
    REVIEW_STATUS_DRAFT,
    VALID_REVIEW_STATUSES,
    _compute_fingerprint,
    normalize_topic_key,
)
from ..models.sqe import QuestionOption
from ..models.subject import Subject
from ..services.csv_import import (
    stage_import,
    confirm_import,
    get_template_csv,
    recompute_bank_counts,
)


def _admin_required(f):
    """Decorator to ensure user is admin."""
    from functools import wraps

    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin():
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

        import_mode = request.form.get("import_mode", "replace")
        if import_mode not in ("replace", "append"):
            import_mode = "replace"

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

        return redirect(
            url_for("quiz_admin.preview_import", token=staged.token, mode=import_mode)
        )

    active_bank = QuestionBankImport.query.filter_by(active=True).first()
    return render_template("admin/upload.html", active_bank=active_bank)


@admin_bp.route("/template")
@_admin_required
def download_template():
    """Download a fillable SQE question CSV template."""
    csv_data = get_template_csv(include_examples=True).encode("utf-8")
    return send_file(
        io.BytesIO(csv_data),
        mimetype="text/csv",
        as_attachment=True,
        download_name="sqe_question_template.csv",
    )


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

    import_mode = request.args.get("mode", "replace")
    if import_mode not in ("replace", "append"):
        import_mode = "replace"

    return render_template(
        "admin/preview.html",
        staged=staged,
        active_bank=active_bank,
        import_mode=import_mode,
    )


@admin_bp.route("/confirm/<token>", methods=["POST"])
@_admin_required
def confirm_import_route(token):
    """Confirm and activate the staged import."""
    import_mode = request.form.get("import_mode", "replace")
    if import_mode not in ("replace", "append"):
        import_mode = "replace"
    try:
        new_bank = confirm_import(token, current_user.id, mode=import_mode)
        verb = "appended into" if import_mode == "append" else "activated as"
        flash(
            f"Import complete: bank now {verb} {new_bank.question_count} questions "
            f"across {new_bank.topic_count} topics.",
            "success",
        )
        return redirect(url_for("quiz_admin.list_questions"))
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


# ---------------------------------------------------------------------------
# In-site question editor
# ---------------------------------------------------------------------------

_OPTION_LETTERS = ["A", "B", "C", "D", "E"]


def _get_or_create_active_bank() -> QuestionBankImport:
    """Return the active bank, creating an empty manual-entry bank if none exists."""
    bank = QuestionBankImport.query.filter_by(active=True).first()
    if bank is None:
        bank = QuestionBankImport(
            importer_user_id=current_user.id,
            filename="Manual entries",
            checksum="manual",
            detected_delimiter=",",
            detected_encoding="UTF-8",
            active=True,
            status="active",
        )
        db.session.add(bank)
        db.session.flush()
    return bank


def _apply_question_form(q: Question, form) -> list[str]:
    """Populate a Question from submitted form data. Returns a list of error strings."""
    errors: list[str] = []

    qid = (form.get("external_question_id") or "").strip()
    subject_id = form.get("subject_id", type=int)
    subject = Subject.query.get(subject_id) if subject_id else None
    question_text = (form.get("question_text") or "").strip()
    answers = {L: (form.get(f"answer_{L.lower()}") or "").strip() for L in _OPTION_LETTERS}
    correct = (form.get("correct_answer") or "").strip().upper()

    if not qid:
        errors.append("Question ID is required.")
    if subject is None:
        errors.append("A valid subject/topic must be selected.")
    if not question_text:
        errors.append("Question text is required.")

    filled = [L for L in _OPTION_LETTERS if answers[L]]
    if len(filled) < 2:
        errors.append("At least two answer options must be filled in.")
    if correct not in filled:
        errors.append("Correct Answer must match one of the filled answer options.")

    # Uniqueness of Question ID within the active bank (exclude self)
    if qid and q.bank_import_id:
        clash = Question.query.filter(
            Question.bank_import_id == q.bank_import_id,
            Question.external_question_id == qid,
            Question.id != (q.id or 0),
        ).first()
        if clash:
            errors.append(f"Question ID '{qid}' already exists in the active bank.")

    if errors:
        return errors

    difficulty = (form.get("difficulty") or "").strip().lower() or None
    if difficulty not in (None, "easy", "medium", "hard"):
        difficulty = None
    review_status = (form.get("review_status") or "").strip()
    if review_status not in VALID_REVIEW_STATUSES:
        review_status = REVIEW_STATUS_DRAFT

    q.external_question_id = qid
    q.subject_id = subject.id
    q.paper = subject.paper
    q.topic = subject.full_name
    q.topic_key = normalize_topic_key(subject.full_name)
    q.subtopic = (form.get("subtopic") or "").strip() or None
    q.question_text = question_text
    q.answer_a = answers["A"] or None
    q.answer_b = answers["B"] or None
    q.answer_c = answers["C"] or None
    q.answer_d = answers["D"] or None
    q.answer_e = answers["E"] or None
    q.correct_answer = correct
    q.question_format = FORMAT_SQE5
    q.explanation = (form.get("explanation") or "").strip() or None
    q.authority = (form.get("authority") or "").strip() or None
    q.difficulty = difficulty
    q.review_status = review_status
    q.active = bool(form.get("active"))

    # Rebuild normalised options (replaces any existing via delete-orphan cascade)
    q.options = [
        QuestionOption(
            source_label=L,
            option_text=answers[L],
            is_correct=(L == correct),
            source_order=idx,
        )
        for idx, L in enumerate(filled)
    ]
    q.content_fingerprint = q.compute_fingerprint()
    return []


@admin_bp.route("/questions")
@_admin_required
def list_questions():
    """Audit view: list all questions in the active bank, with filters."""
    active_bank = QuestionBankImport.query.filter_by(active=True).first()
    subjects = Subject.query.order_by(Subject.paper, Subject.display_order).all()

    filter_paper = request.args.get("paper") or ""
    filter_subject_id = request.args.get("subject_id", type=int)
    search = (request.args.get("q") or "").strip()

    questions = []
    if active_bank:
        query = Question.query.filter_by(bank_import_id=active_bank.id)
        if filter_paper in ("FLK1", "FLK2"):
            query = query.filter_by(paper=filter_paper)
        if filter_subject_id:
            query = query.filter_by(subject_id=filter_subject_id)
        if search:
            query = query.filter(Question.question_text.ilike(f"%{search}%"))
        questions = query.order_by(
            Question.paper, Question.topic_key, Question.external_question_id
        ).all()

    return render_template(
        "admin/questions.html",
        active_bank=active_bank,
        questions=questions,
        subjects=subjects,
        filter_paper=filter_paper,
        filter_subject_id=filter_subject_id,
        search=search,
    )


@admin_bp.route("/questions/new", methods=["GET", "POST"])
@_admin_required
def new_question():
    """Add a single question to the active bank."""
    subjects = Subject.query.order_by(Subject.paper, Subject.display_order).all()

    if request.method == "POST":
        bank = _get_or_create_active_bank()
        q = Question(bank_import_id=bank.id)
        errors = _apply_question_form(q, request.form)
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template(
                "admin/question_form.html",
                subjects=subjects,
                question=None,
                form=request.form,
                mode="new",
                review_statuses=VALID_REVIEW_STATUSES,
                option_letters=_OPTION_LETTERS,
            )
        db.session.add(q)
        db.session.flush()
        recompute_bank_counts(bank)
        db.session.commit()
        flash(f"Question '{q.external_question_id}' added.", "success")
        return redirect(url_for("quiz_admin.list_questions"))

    return render_template(
        "admin/question_form.html",
        subjects=subjects,
        question=None,
        form={},
        mode="new",
        review_statuses=VALID_REVIEW_STATUSES,
        option_letters=_OPTION_LETTERS,
    )


@admin_bp.route("/questions/<int:question_id>/edit", methods=["GET", "POST"])
@_admin_required
def edit_question(question_id):
    """Edit an existing question."""
    q = Question.query.get_or_404(question_id)
    subjects = Subject.query.order_by(Subject.paper, Subject.display_order).all()

    if request.method == "POST":
        errors = _apply_question_form(q, request.form)
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template(
                "admin/question_form.html",
                subjects=subjects,
                question=q,
                form=request.form,
                mode="edit",
                review_statuses=VALID_REVIEW_STATUSES,
                option_letters=_OPTION_LETTERS,
            )
        recompute_bank_counts(q.bank_import)
        db.session.commit()
        flash(f"Question '{q.external_question_id}' updated.", "success")
        return redirect(url_for("quiz_admin.list_questions"))

    return render_template(
        "admin/question_form.html",
        subjects=subjects,
        question=q,
        form=None,
        mode="edit",
        review_statuses=VALID_REVIEW_STATUSES,
        option_letters=_OPTION_LETTERS,
    )


@admin_bp.route("/questions/<int:question_id>/delete", methods=["POST"])
@_admin_required
def delete_question(question_id):
    """Delete a question from the active bank."""
    q = Question.query.get_or_404(question_id)
    bank = q.bank_import
    qid = q.external_question_id
    db.session.delete(q)
    db.session.flush()
    if bank:
        recompute_bank_counts(bank)
    db.session.commit()
    flash(f"Question '{qid}' deleted.", "success")
    return redirect(url_for("quiz_admin.list_questions"))


