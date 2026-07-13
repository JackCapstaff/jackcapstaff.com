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
from sqlalchemy import select

from . import testing_bp
from ..extensions import db
from ..models.session import TestSession, TestSessionQuestion
from ..models.question import QuestionBankImport, Question, FORMAT_SQE5
from ..models.sqe import QuestionOption, TestSessionOption
from ..models.specification import BlueprintProfile
from ..services.question_selection import (
    select_fresh_questions,
    select_focused_questions,
    get_active_subject_counts,
)
from ..services.adaptive_selection import select_adaptive_questions
from ..services.blueprint_selection import select_sqe_questions, check_blueprint_coverage

# SQE full-paper timing: 90 questions = 153 minutes
SQE_FULL_PAPER_QUESTIONS = 90
SQE_FULL_PAPER_MINUTES = 153  # 2h 33m per SRA spec

SQE_MODES = ("sqe_blueprint", "sqe_adaptive", "sqe_simulation")
FOCUSED_MODES = ("focused",)
LEGACY_MODES = ("fresh", "adaptive")
ALL_MODES = LEGACY_MODES + FOCUSED_MODES + SQE_MODES


def _snapshot_question(session_obj, q, position, active_bank):
    """Create a TestSessionQuestion snapshot (plus option snapshots for SQE5).

    Content is copied so historic results survive question-bank changes.
    Scoring throughout the app uses the flat answer columns, which the CSV
    importer always populates (including for SQE5 questions).
    """
    subject_name = q.subject.full_name if getattr(q, "subject", None) else None
    tsq = TestSessionQuestion(
        session_id=session_obj.id,
        source_question_id=q.id,
        bank_import_id=active_bank.id,
        external_question_id=q.external_question_id,
        topic=q.topic,
        topic_key=q.topic_key,
        paper=q.paper,
        subject_id_snapshot=q.subject_id,
        subject_name_snapshot=subject_name,
        question_format=q.question_format or FORMAT_SQE5,
        question_text=q.question_text,
        answer_a=q.answer_a,
        answer_b=q.answer_b,
        answer_c=q.answer_c,
        answer_d=q.answer_d,
        answer_e=q.answer_e,
        correct_answer=q.correct_answer,
        explanation=q.explanation,
        reference=q.reference,
        difficulty=q.difficulty,
        content_fingerprint=q.content_fingerprint,
        display_position=position,
        source_type_snapshot=q.source_type,
        source_notice_snapshot=q.source_notice,
    )
    db.session.add(tsq)
    db.session.flush()

    # Snapshot normalised options for SQE5 questions (used for display/audit).
    if q.question_format == FORMAT_SQE5:
        options = db.session.execute(
            select(QuestionOption)
            .where(QuestionOption.question_id == q.id)
            .order_by(QuestionOption.source_order)
        ).scalars().all()
        display_letters = list("ABCDE")
        for opt_idx, opt in enumerate(options):
            db.session.add(
                TestSessionOption(
                    test_session_question_id=tsq.id,
                    original_option_id=opt.id,
                    display_label=display_letters[opt_idx],
                    option_text_snapshot=opt.option_text,
                    is_correct=opt.is_correct,
                    display_order=opt_idx,
                )
            )
    return tsq


@testing_bp.route("/start", methods=["GET", "POST"])
@login_required
def start_test():
    """Start a new test session."""
    if request.method == "POST":
        test_mode = request.form.get("mode")
        time_limit = request.form.get("time_limit")
        question_count = int(request.form.get("question_count", 25))
        topics = request.form.getlist("topics")
        paper = request.form.get("paper")  # FLK1 or FLK2 for SQE modes

        if test_mode not in ALL_MODES:
            flash("Invalid test mode.", "error")
            return redirect(url_for("quiz_main.dashboard"))

        is_sqe_mode = test_mode in SQE_MODES

        # Validate paper for SQE modes
        if is_sqe_mode and paper not in ("FLK1", "FLK2"):
            flash("Please select FLK1 or FLK2 for SQE practice.", "error")
            return redirect(url_for("quiz_testing.start_test"))

        # SQE simulation: lock to 90 questions and full-paper timer
        if test_mode == "sqe_simulation":
            question_count = SQE_FULL_PAPER_QUESTIONS
            time_limit = str(SQE_FULL_PAPER_MINUTES)

        # Validate time limit
        min_limit = current_app.config["MIN_TIME_LIMIT_MINUTES"]
        max_limit = current_app.config["MAX_TIME_LIMIT_MINUTES"]

        timed = time_limit != "untimed"
        time_limit_minutes = int(time_limit) if timed else None

        if timed and (time_limit_minutes < min_limit or time_limit_minutes > max_limit):
            flash(f"Time limit must be between {min_limit} and {max_limit} minutes.", "error")
            return redirect(url_for("quiz_testing.start_test"))

        # Get active question bank
        active_bank = QuestionBankImport.query.filter_by(active=True).first()
        if not active_bank:
            flash("No active question bank. Please upload one.", "error")
            return redirect(url_for("quiz_main.dashboard"))

        seed = random.randint(0, 2**31 - 1)

        # ---------------------------------------------------------------
        # SQE blueprint / adaptive / simulation modes
        # ---------------------------------------------------------------
        if is_sqe_mode:
            profile = (
                db.session.execute(
                    select(BlueprintProfile).where(
                        BlueprintProfile.paper == paper,
                        BlueprintProfile.active == True,  # noqa: E712
                    )
                )
                .scalars()
                .first()
            )
            if not profile:
                flash(f"No active blueprint profile found for {paper}.", "error")
                return redirect(url_for("quiz_testing.start_test"))

            # Check coverage before selecting
            shortfalls = check_blueprint_coverage(profile, question_count, strict=False)
            coverage_warnings = [
                f"{code}: need {n} more questions" for code, n in shortfalls.items()
            ]

            try:
                selected_items, allocation = select_sqe_questions(
                    profile=profile,
                    count=question_count,
                    user_id=current_user.id,
                    mode=test_mode,
                    seed=seed,
                    strict=(test_mode == "sqe_simulation"),
                )
            except ValueError as e:
                flash(str(e), "error")
                return redirect(url_for("quiz_testing.start_test"))

            if not selected_items:
                if allocation.shortfall_by_subject:
                    detail = ", ".join(
                        f"{k}: needs {v} more" for k, v in allocation.shortfall_by_subject.items()
                    )
                    flash(
                        f"Not enough questions for strict blueprint. Shortfalls: {detail}.",
                        "error",
                    )
                else:
                    flash("Not enough questions available for this test.", "error")
                return redirect(url_for("quiz_testing.start_test"))

            # Load full Question objects for snapshotting
            q_ids = [item.question_id for item in selected_items]
            questions_by_id: dict[int, Question] = {
                q.id: q
                for q in db.session.execute(
                    select(Question).where(Question.id.in_(q_ids))
                ).scalars().all()
            }

            import json as _json
            session_obj = TestSession(
                user_id=current_user.id,
                mode=test_mode,
                paper=paper,
                blueprint_profile_id=profile.id,
                question_count=len(selected_items),
                timed=timed,
                time_limit_seconds=time_limit_minutes * 60 if timed else None,
                random_seed=seed,
                bank_import_id=active_bank.id,
                status="in_progress",
                blueprint_allocation_snapshot=_json.dumps(
                    {
                        sa.subject_code: sa.allocated
                        for sa in allocation.allocations
                    }
                ),
                is_strict_blueprint=(test_mode == "sqe_simulation"),
            )
            db.session.add(session_obj)
            db.session.flush()

            for item in selected_items:
                q = questions_by_id.get(item.question_id)
                if not q:
                    continue
                _snapshot_question(session_obj, q, item.display_position, active_bank)

            if coverage_warnings:
                for w in coverage_warnings:
                    flash(f"Coverage warning: {w}", "warning")
            if allocation.warnings:
                for w in allocation.warnings[:3]:
                    flash(w, "warning")

        # ---------------------------------------------------------------
        # Focused practice: user-chosen subjects (modules) and/or topics
        # ---------------------------------------------------------------
        elif test_mode == "focused":
            subject_ids = [
                int(s) for s in request.form.getlist("subjects") if s.strip().isdigit()
            ]
            topic_keys = topics  # from request.form.getlist("topics") above

            if not subject_ids and not topic_keys:
                flash("Select at least one module or topic to practise.", "error")
                return redirect(url_for("quiz_testing.start_test"))

            try:
                selected_qs = select_focused_questions(
                    requested=question_count,
                    subject_ids=subject_ids or None,
                    topic_keys=topic_keys or None,
                    seed=seed,
                )
            except ValueError as e:
                flash(str(e), "error")
                return redirect(url_for("quiz_testing.start_test"))

            if not selected_qs:
                flash("No questions match your selection.", "error")
                return redirect(url_for("quiz_testing.start_test"))

            if len(selected_qs) < question_count:
                flash(
                    f"Only {len(selected_qs)} question(s) available for your selection.",
                    "warning",
                )

            session_obj = TestSession(
                user_id=current_user.id,
                mode="focused",
                question_count=len(selected_qs),
                selected_topics=topic_keys if topic_keys else None,
                timed=timed,
                time_limit_seconds=time_limit_minutes * 60 if timed else None,
                random_seed=seed,
                bank_import_id=active_bank.id,
                status="in_progress",
            )
            db.session.add(session_obj)
            db.session.flush()

            for idx, question in enumerate(selected_qs):
                _snapshot_question(session_obj, question, idx, active_bank)

        # ---------------------------------------------------------------
        # Legacy modes (fresh / adaptive)
        # ---------------------------------------------------------------
        else:
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
        topic_rows = db.session.execute(
            select(Question.topic_key)
            .where(
                Question.bank_import_id == active_bank.id,
                Question.active == True,  # noqa: E712
            )
            .distinct()
        ).scalars().all()
        topics = sorted(tk for tk in topic_rows if tk)

    # Load SQE blueprint profiles for display
    flk1_profile = db.session.execute(
        select(BlueprintProfile).where(
            BlueprintProfile.paper == "FLK1", BlueprintProfile.active == True  # noqa: E712
        )
    ).scalars().first()
    flk2_profile = db.session.execute(
        select(BlueprintProfile).where(
            BlueprintProfile.paper == "FLK2", BlueprintProfile.active == True  # noqa: E712
        )
    ).scalars().first()

    # Load SQE subjects (modules) that have questions, for focused practice
    subjects = get_active_subject_counts() if active_bank else []

    return render_template(
        "testing/start_test.html",
        active_bank=active_bank,
        topics=topics,
        subjects=subjects,
        min_limit=current_app.config["MIN_TIME_LIMIT_MINUTES"],
        max_limit=current_app.config["MAX_TIME_LIMIT_MINUTES"],
        flk1_profile=flk1_profile,
        flk2_profile=flk2_profile,
        sqe_full_minutes=SQE_FULL_PAPER_MINUTES,
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

    # Update current_position from query param
    pos = request.args.get("pos", type=int)
    if pos is not None:
        clamped = max(0, min(pos, session_obj.question_count - 1))
        if session_obj.current_position != clamped:
            session_obj.current_position = clamped
            db.session.commit()

    return render_template(
        "testing/take_test.html",
        session=session_obj,
        topic_visible=current_app.config["TOPIC_VISIBLE_DURING_TEST"],
        is_sqe_mode=session_obj.mode in SQE_MODES,
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
    answer = data.get("answer")  # A–E or None

    valid_answers = ("A", "B", "C", "D", "E")
    if answer and answer not in valid_answers:
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

    session_obj.status = "submitted"
    session_obj.submission_reason = "manual"
    session_obj.submitted_at = datetime.now(timezone.utc)

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


