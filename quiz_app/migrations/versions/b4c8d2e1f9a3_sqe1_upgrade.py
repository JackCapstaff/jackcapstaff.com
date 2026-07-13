"""SQE1 upgrade: new tables, columns, and seed data

Revision ID: b4c8d2e1f9a3
Revises: a33e27a5a327
Create Date: 2026-07-13 12:00:00.000000

Adds all SQE1 model changes to the existing schema:
- New tables: subjects, tags, question_tags, assessment_specifications,
  exam_windows, blueprint_profiles, blueprint_subjects, question_options,
  staged_options, test_session_options, user_answers, question_attempts,
  user_subject_stats, user_tag_stats, audit_logs
- New columns on existing tables (all nullable / with defaults)
- Does NOT drop any existing columns (backward-compatible)
- Seeds canonical SQE1 subjects, tags, spec, and blueprint profiles
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "b4c8d2e1f9a3"
down_revision = "a33e27a5a327"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------

def upgrade() -> None:
    conn = op.get_bind()

    # ------------------------------------------------------------------
    # 1. NEW TABLES (independent of existing tables first)
    # ------------------------------------------------------------------

    op.create_table(
        "assessment_specifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("source_url", sa.String(512), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    with op.batch_alter_table("assessment_specifications") as batch_op:
        batch_op.create_index("ix_aspec_active", ["active"])
        batch_op.create_index("ix_aspec_effective_from", ["effective_from"])
        batch_op.create_index("ix_aspec_effective_to", ["effective_to"])

    op.create_table(
        "subjects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False, unique=True),
        sa.Column("paper", sa.String(4), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("short_name", sa.String(80), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    with op.batch_alter_table("subjects") as batch_op:
        batch_op.create_index("ix_subjects_code", ["code"], unique=True)
        batch_op.create_index("ix_subjects_paper", ["paper"])

    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(60), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("category", sa.String(60), nullable=True),
        sa.Column("is_cross_cutting", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("is_flk1_only", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    with op.batch_alter_table("tags") as batch_op:
        batch_op.create_index("ix_tags_code", ["code"], unique=True)

    op.create_table(
        "exam_windows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("first_assessment_date", sa.Date(), nullable=True),
        sa.Column("law_cutoff_date", sa.Date(), nullable=True),
        sa.Column(
            "specification_id",
            sa.Integer(),
            sa.ForeignKey("assessment_specifications.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    with op.batch_alter_table("exam_windows") as batch_op:
        batch_op.create_index("ix_ewins_first_date", ["first_assessment_date"])
        batch_op.create_index("ix_ewins_spec", ["specification_id"])
        batch_op.create_index("ix_ewins_active", ["active"])

    op.create_table(
        "blueprint_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "specification_id",
            sa.Integer(),
            sa.ForeignKey("assessment_specifications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("paper", sa.String(4), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("strict_min_questions", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("cross_cutting_cap", sa.Numeric(5, 4), nullable=False, server_default="0.2000"),
        sa.Column("allow_option_randomise", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    with op.batch_alter_table("blueprint_profiles") as batch_op:
        batch_op.create_index("ix_bpro_spec", ["specification_id"])
        batch_op.create_index("ix_bpro_active", ["active"])

    op.create_table(
        "blueprint_subjects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("blueprint_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subject_id",
            sa.Integer(),
            sa.ForeignKey("subjects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("min_pct", sa.Numeric(5, 4), nullable=False),
        sa.Column("max_pct", sa.Numeric(5, 4), nullable=False),
        sa.Column("target_pct", sa.Numeric(5, 4), nullable=False),
    )
    with op.batch_alter_table("blueprint_subjects") as batch_op:
        batch_op.create_index("ix_bsub_profile", ["profile_id"])
        batch_op.create_index("ix_bsub_subject", ["subject_id"])

    op.create_table(
        "question_tags",
        sa.Column(
            "question_id",
            sa.Integer(),
            sa.ForeignKey("questions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id",
            sa.Integer(),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    op.create_table(
        "question_options",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "question_id",
            sa.Integer(),
            sa.ForeignKey("questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_label", sa.String(1), nullable=False),
        sa.Column("option_text", sa.Text(), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("source_order", sa.Integer(), nullable=False),
    )
    with op.batch_alter_table("question_options") as batch_op:
        batch_op.create_index("ix_qopt_question_id", ["question_id"])

    op.create_table(
        "staged_options",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "staged_question_id",
            sa.Integer(),
            sa.ForeignKey("staged_questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_label", sa.String(1), nullable=False),
        sa.Column("option_text", sa.Text(), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("source_order", sa.Integer(), nullable=False),
    )
    with op.batch_alter_table("staged_options") as batch_op:
        batch_op.create_index("ix_sopt_staged_question_id", ["staged_question_id"])

    op.create_table(
        "test_session_options",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "test_session_question_id",
            sa.Integer(),
            sa.ForeignKey("test_session_questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "original_option_id",
            sa.Integer(),
            sa.ForeignKey("question_options.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("display_label", sa.String(1), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("option_text_snapshot", sa.Text(), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=False, server_default="0"),
    )
    with op.batch_alter_table("test_session_options") as batch_op:
        batch_op.create_index("ix_tsopt_tsq_id", ["test_session_question_id"])

    op.create_table(
        "user_answers",
        sa.Column(
            "test_session_question_id",
            sa.Integer(),
            sa.ForeignKey("test_session_questions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "selected_session_option_id",
            sa.Integer(),
            sa.ForeignKey("test_session_options.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("change_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
    )

    op.create_table(
        "question_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("logical_question_key", sa.String(200), nullable=False),
        sa.Column(
            "question_version_id",
            sa.Integer(),
            sa.ForeignKey("questions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "test_session_id",
            sa.Integer(),
            sa.ForeignKey("test_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "subject_id",
            sa.Integer(),
            sa.ForeignKey("subjects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", sa.String(12), nullable=False),
        sa.Column("response_time_seconds", sa.Float(), nullable=True),
        sa.Column(
            "consecutive_correct_snapshot", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "consecutive_incorrect_snapshot", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    with op.batch_alter_table("question_attempts") as batch_op:
        batch_op.create_index("ix_qa_user_id", ["user_id"])
        batch_op.create_index("ix_qa_user_key", ["user_id", "logical_question_key"])
        batch_op.create_index("ix_qa_user_subject", ["user_id", "subject_id"])
        batch_op.create_index("ix_qa_attempted_at", ["attempted_at"])

    op.create_table(
        "user_subject_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "subject_id",
            sa.Integer(),
            sa.ForeignKey("subjects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recent_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recent_correct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_attempted", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("user_id", "subject_id", name="uq_uss_user_subject"),
    )
    with op.batch_alter_table("user_subject_stats") as batch_op:
        batch_op.create_index("ix_uss_user_id", ["user_id"])
        batch_op.create_index("ix_uss_subject_id", ["subject_id"])

    op.create_table(
        "user_tag_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tag_id",
            sa.Integer(),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recent_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recent_correct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_attempted", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("user_id", "tag_id", name="uq_uts_user_tag"),
    )
    with op.batch_alter_table("user_tag_stats") as batch_op:
        batch_op.create_index("ix_uts_user_id", ["user_id"])
        batch_op.create_index("ix_uts_tag_id", ["tag_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("entity_type", sa.String(60), nullable=True),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("before_summary", sa.Text(), nullable=True),
        sa.Column("after_summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.create_index("ix_audit_actor_user_id", ["actor_user_id"])
        batch_op.create_index("ix_audit_action", ["action"])
        batch_op.create_index("ix_audit_created_at", ["created_at"])

    # ------------------------------------------------------------------
    # 2. ADD COLUMNS TO EXISTING TABLES
    # ------------------------------------------------------------------

    # question_bank_imports
    with op.batch_alter_table("question_bank_imports") as batch_op:
        batch_op.add_column(sa.Column("detected_encoding", sa.String(20), nullable=True))
        batch_op.add_column(
            sa.Column("flk1_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("flk2_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("active_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("inactive_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column(
                "specification_id",
                sa.Integer(),
                sa.ForeignKey("assessment_specifications.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
        batch_op.add_column(sa.Column("spec_version_snapshot", sa.String(120), nullable=True))
        batch_op.add_column(sa.Column("import_notes", sa.Text(), nullable=True))
        batch_op.create_index("ix_qbi_specification_id", ["specification_id"])

    # staged_imports
    with op.batch_alter_table("staged_imports") as batch_op:
        batch_op.add_column(sa.Column("detected_encoding", sa.String(20), nullable=True))
        batch_op.add_column(
            sa.Column("flk1_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("flk2_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column(
                "specification_id",
                sa.Integer(),
                sa.ForeignKey("assessment_specifications.id", ondelete="SET NULL"),
                nullable=True,
            )
        )

    # questions: make answer_a-d nullable, add SQE columns
    with op.batch_alter_table("questions") as batch_op:
        batch_op.alter_column("answer_a", existing_type=sa.Text(), nullable=True)
        batch_op.alter_column("answer_b", existing_type=sa.Text(), nullable=True)
        batch_op.alter_column("answer_c", existing_type=sa.Text(), nullable=True)
        batch_op.alter_column("answer_d", existing_type=sa.Text(), nullable=True)
        batch_op.alter_column("correct_answer", existing_type=sa.String(1), nullable=True)
        batch_op.add_column(
            sa.Column(
                "question_format",
                sa.String(16),
                nullable=False,
                server_default="LEGACY_MCQ4",
            )
        )
        batch_op.add_column(sa.Column("paper", sa.String(4), nullable=True))
        batch_op.add_column(
            sa.Column(
                "subject_id",
                sa.Integer(),
                sa.ForeignKey("subjects.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
        batch_op.add_column(sa.Column("subtopic", sa.String(255), nullable=True))
        batch_op.add_column(sa.Column("logical_key", sa.String(300), nullable=True))
        batch_op.add_column(sa.Column("answer_e", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("explanation_source", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("explanation_author", sa.String(255), nullable=True))
        batch_op.add_column(
            sa.Column(
                "explanation_independent", sa.Boolean(), nullable=False, server_default="1"
            )
        )
        batch_op.add_column(sa.Column("source_type", sa.String(60), nullable=True))
        batch_op.add_column(sa.Column("source_set", sa.String(120), nullable=True))
        batch_op.add_column(sa.Column("source_question_id", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("source_version", sa.String(80), nullable=True))
        batch_op.add_column(sa.Column("source_url", sa.String(512), nullable=True))
        batch_op.add_column(sa.Column("source_notice", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("authority", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("candidate_correct_pct", sa.Numeric(5, 2), nullable=True))
        batch_op.add_column(sa.Column("law_cutoff_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("valid_from", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("valid_to", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("last_reviewed", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("reviewed_by", sa.String(255), nullable=True))
        batch_op.add_column(
            sa.Column("review_status", sa.String(40), nullable=False, server_default="Draft")
        )
        batch_op.add_column(sa.Column("review_notes", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("language", sa.String(10), nullable=False, server_default="en")
        )
        batch_op.add_column(sa.Column("notes", sa.Text(), nullable=True))
        batch_op.create_index("ix_questions_paper", ["paper"])
        batch_op.create_index("ix_questions_subject_id", ["subject_id"])
        batch_op.create_index("ix_questions_logical_key", ["logical_key"])
        batch_op.create_index("ix_questions_review_status", ["review_status"])
        batch_op.create_index("ix_questions_source_type", ["source_type"])
        batch_op.create_index("ix_questions_question_format", ["question_format"])

    # Mark all existing questions as LEGACY_MCQ4 with review_status = Official Source placeholder
    # (They have no paper/subject, so they can't yet be used in SQE modes)
    conn.execute(
        text(
            "UPDATE questions SET question_format = 'LEGACY_MCQ4', review_status = 'Draft'"
            " WHERE question_format IS NULL OR question_format = 'LEGACY_MCQ4'"
        )
    )

    # staged_questions: make answer_a-d nullable, add SQE columns
    with op.batch_alter_table("staged_questions") as batch_op:
        batch_op.alter_column("answer_a", existing_type=sa.Text(), nullable=True)
        batch_op.alter_column("answer_b", existing_type=sa.Text(), nullable=True)
        batch_op.alter_column("answer_c", existing_type=sa.Text(), nullable=True)
        batch_op.alter_column("answer_d", existing_type=sa.Text(), nullable=True)
        batch_op.alter_column("correct_answer", existing_type=sa.String(1), nullable=True)
        batch_op.add_column(
            sa.Column(
                "question_format",
                sa.String(16),
                nullable=False,
                server_default="LEGACY_MCQ4",
            )
        )
        batch_op.add_column(sa.Column("paper", sa.String(4), nullable=True))
        batch_op.add_column(
            sa.Column(
                "subject_id",
                sa.Integer(),
                sa.ForeignKey("subjects.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
        batch_op.add_column(sa.Column("subtopic", sa.String(255), nullable=True))
        batch_op.add_column(sa.Column("logical_key", sa.String(300), nullable=True))
        batch_op.add_column(sa.Column("answer_e", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("explanation_source", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("explanation_author", sa.String(255), nullable=True))
        batch_op.add_column(
            sa.Column(
                "explanation_independent", sa.Boolean(), nullable=False, server_default="1"
            )
        )
        batch_op.add_column(sa.Column("source_type", sa.String(60), nullable=True))
        batch_op.add_column(sa.Column("source_set", sa.String(120), nullable=True))
        batch_op.add_column(sa.Column("source_question_id", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("source_version", sa.String(80), nullable=True))
        batch_op.add_column(sa.Column("source_url", sa.String(512), nullable=True))
        batch_op.add_column(sa.Column("source_notice", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("authority", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("candidate_correct_pct", sa.Numeric(5, 2), nullable=True))
        batch_op.add_column(sa.Column("law_cutoff_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("valid_from", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("valid_to", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("last_reviewed", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("reviewed_by", sa.String(255), nullable=True))
        batch_op.add_column(
            sa.Column("review_status", sa.String(40), nullable=False, server_default="Draft")
        )
        batch_op.add_column(
            sa.Column("language", sa.String(10), nullable=False, server_default="en")
        )
        batch_op.add_column(sa.Column("notes", sa.Text(), nullable=True))

    # test_sessions: widen mode, add SQE columns
    with op.batch_alter_table("test_sessions") as batch_op:
        batch_op.alter_column(
            "mode", existing_type=sa.String(20), type_=sa.String(40), nullable=False
        )
        batch_op.add_column(sa.Column("paper", sa.String(4), nullable=True))
        batch_op.add_column(
            sa.Column(
                "blueprint_profile_id",
                sa.Integer(),
                sa.ForeignKey("blueprint_profiles.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "specification_id",
                sa.Integer(),
                sa.ForeignKey("assessment_specifications.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
        batch_op.add_column(sa.Column("spec_version_snapshot", sa.String(120), nullable=True))
        batch_op.add_column(
            sa.Column("blueprint_allocation_snapshot", sa.JSON(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "is_strict_blueprint", sa.Boolean(), nullable=False, server_default="0"
            )
        )
        batch_op.add_column(
            sa.Column("section_count", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.create_index("ix_test_sessions_paper", ["paper"])

    # test_session_questions: make answer_a-d nullable, add SQE columns
    with op.batch_alter_table("test_session_questions") as batch_op:
        batch_op.alter_column("answer_a", existing_type=sa.Text(), nullable=True)
        batch_op.alter_column("answer_b", existing_type=sa.Text(), nullable=True)
        batch_op.alter_column("answer_c", existing_type=sa.Text(), nullable=True)
        batch_op.alter_column("answer_d", existing_type=sa.Text(), nullable=True)
        batch_op.alter_column("correct_answer", existing_type=sa.String(1), nullable=True)
        batch_op.add_column(sa.Column("paper", sa.String(4), nullable=True))
        batch_op.add_column(sa.Column("subject_id_snapshot", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("subject_name_snapshot", sa.String(255), nullable=True))
        batch_op.add_column(
            sa.Column(
                "question_format",
                sa.String(16),
                nullable=False,
                server_default="LEGACY_MCQ4",
            )
        )
        batch_op.add_column(
            sa.Column("section_number", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.add_column(sa.Column("answer_e", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("source_type_snapshot", sa.String(60), nullable=True))
        batch_op.add_column(sa.Column("source_notice_snapshot", sa.Text(), nullable=True))

    # ------------------------------------------------------------------
    # 3. SEED DATA: Canonical SQE1 subjects
    # ------------------------------------------------------------------

    subjects_table = sa.table(
        "subjects",
        sa.column("code", sa.String),
        sa.column("paper", sa.String),
        sa.column("full_name", sa.String),
        sa.column("short_name", sa.String),
        sa.column("display_order", sa.Integer),
        sa.column("active", sa.Boolean),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )

    _now_dt = datetime.now(timezone.utc)

    op.bulk_insert(
        subjects_table,
        [
            {
                "code": "FLK1_BLP",
                "paper": "FLK1",
                "full_name": "Business Law and Practice",
                "short_name": "Business Law",
                "display_order": 1,
                "active": True,
                "created_at": _now_dt,
            },
            {
                "code": "FLK1_DR",
                "paper": "FLK1",
                "full_name": "Dispute Resolution",
                "short_name": "Dispute Resolution",
                "display_order": 2,
                "active": True,
                "created_at": _now_dt,
            },
            {
                "code": "FLK1_CON",
                "paper": "FLK1",
                "full_name": "Contract Law",
                "short_name": "Contract",
                "display_order": 3,
                "active": True,
                "created_at": _now_dt,
            },
            {
                "code": "FLK1_TORT",
                "paper": "FLK1",
                "full_name": "Tort",
                "short_name": "Tort",
                "display_order": 4,
                "active": True,
                "created_at": _now_dt,
            },
            {
                "code": "FLK1_LS",
                "paper": "FLK1",
                "full_name": "The Legal System of England and Wales",
                "short_name": "Legal System",
                "display_order": 5,
                "active": True,
                "created_at": _now_dt,
            },
            {
                "code": "FLK1_LSVC",
                "paper": "FLK1",
                "full_name": "Legal Services",
                "short_name": "Legal Services",
                "display_order": 6,
                "active": True,
                "created_at": _now_dt,
            },
            {
                "code": "FLK2_PLP",
                "paper": "FLK2",
                "full_name": "Property Law and Practice",
                "short_name": "Property",
                "display_order": 1,
                "active": True,
                "created_at": _now_dt,
            },
            {
                "code": "FLK2_LAND",
                "paper": "FLK2",
                "full_name": "Land Law",
                "short_name": "Land Law",
                "display_order": 2,
                "active": True,
                "created_at": _now_dt,
            },
            {
                "code": "FLK2_WILLS",
                "paper": "FLK2",
                "full_name": "Wills and the Administration of Estates",
                "short_name": "Wills & Estates",
                "display_order": 3,
                "active": True,
                "created_at": _now_dt,
            },
            {
                "code": "FLK2_TRUST",
                "paper": "FLK2",
                "full_name": "Trusts Law",
                "short_name": "Trusts",
                "display_order": 4,
                "active": True,
                "created_at": _now_dt,
            },
            {
                "code": "FLK2_CRIM",
                "paper": "FLK2",
                "full_name": "Criminal Liability",
                "short_name": "Criminal Liability",
                "display_order": 5,
                "active": True,
                "created_at": _now_dt,
            },
            {
                "code": "FLK2_CRIMP",
                "paper": "FLK2",
                "full_name": "Criminal Law and Practice",
                "short_name": "Criminal Practice",
                "display_order": 6,
                "active": True,
                "created_at": _now_dt,
            },
        ],
    )

    # ------------------------------------------------------------------
    # 4. SEED DATA: Canonical cross-cutting tags
    # ------------------------------------------------------------------

    tags_table = sa.table(
        "tags",
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("category", sa.String),
        sa.column("is_cross_cutting", sa.Boolean),
        sa.column("is_flk1_only", sa.Boolean),
        sa.column("active", sa.Boolean),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )

    op.bulk_insert(
        tags_table,
        [
            # Cross-cutting (count towards 20% cap)
            {"code": "ETHICS", "name": "Ethics", "category": "cross_cutting", "is_cross_cutting": True, "is_flk1_only": False, "active": True, "created_at": _now_dt},
            {"code": "PROF_CONDUCT", "name": "Professional Conduct", "category": "cross_cutting", "is_cross_cutting": True, "is_flk1_only": False, "active": True, "created_at": _now_dt},
            {"code": "MONEY_LAUNDERING", "name": "Money Laundering", "category": "cross_cutting", "is_cross_cutting": True, "is_flk1_only": True, "active": True, "created_at": _now_dt},
            # Professional standards
            {"code": "SRA_ACCOUNTS", "name": "Solicitors Accounts", "category": "professional", "is_cross_cutting": False, "is_flk1_only": False, "active": True, "created_at": _now_dt},
            {"code": "CLIENT_CARE", "name": "Client Care", "category": "professional", "is_cross_cutting": False, "is_flk1_only": False, "active": True, "created_at": _now_dt},
            {"code": "CONFLICTS", "name": "Conflicts of Interest", "category": "professional", "is_cross_cutting": False, "is_flk1_only": False, "active": True, "created_at": _now_dt},
            {"code": "CONFIDENTIALITY", "name": "Confidentiality", "category": "professional", "is_cross_cutting": False, "is_flk1_only": False, "active": True, "created_at": _now_dt},
            {"code": "UNDERTAKINGS", "name": "Undertakings", "category": "professional", "is_cross_cutting": False, "is_flk1_only": False, "active": True, "created_at": _now_dt},
            {"code": "SRA_ACCOUNTS_RULES", "name": "SRA Accounts Rules", "category": "professional", "is_cross_cutting": False, "is_flk1_only": False, "active": True, "created_at": _now_dt},
            # FLK1 subjects / procedural
            {"code": "TAXATION", "name": "Taxation", "category": "flk1", "is_cross_cutting": False, "is_flk1_only": False, "active": True, "created_at": _now_dt},
            {"code": "BIZ_ORGS", "name": "Business Organisations", "category": "flk1", "is_cross_cutting": False, "is_flk1_only": False, "active": True, "created_at": _now_dt},
            {"code": "CIVIL_PROC", "name": "Civil Procedure", "category": "flk1", "is_cross_cutting": False, "is_flk1_only": False, "active": True, "created_at": _now_dt},
            {"code": "EVIDENCE", "name": "Evidence", "category": "shared", "is_cross_cutting": False, "is_flk1_only": False, "active": True, "created_at": _now_dt},
            {"code": "REMEDIES", "name": "Remedies", "category": "shared", "is_cross_cutting": False, "is_flk1_only": False, "active": True, "created_at": _now_dt},
            {"code": "COSTS", "name": "Costs", "category": "shared", "is_cross_cutting": False, "is_flk1_only": False, "active": True, "created_at": _now_dt},
            # FLK2 subjects / conveyancing
            {"code": "CONVEYANCING", "name": "Conveyancing", "category": "flk2", "is_cross_cutting": False, "is_flk1_only": False, "active": True, "created_at": _now_dt},
            {"code": "LEASEHOLD", "name": "Leasehold", "category": "flk2", "is_cross_cutting": False, "is_flk1_only": False, "active": True, "created_at": _now_dt},
            {"code": "FREEHOLD", "name": "Freehold", "category": "flk2", "is_cross_cutting": False, "is_flk1_only": False, "active": True, "created_at": _now_dt},
            {"code": "PROBATE", "name": "Probate", "category": "flk2", "is_cross_cutting": False, "is_flk1_only": False, "active": True, "created_at": _now_dt},
            {"code": "INTESTACY", "name": "Intestacy", "category": "flk2", "is_cross_cutting": False, "is_flk1_only": False, "active": True, "created_at": _now_dt},
            {"code": "ESTATE_ADMIN", "name": "Estate Administration", "category": "flk2", "is_cross_cutting": False, "is_flk1_only": False, "active": True, "created_at": _now_dt},
            {"code": "TRUSTEE_DUTIES", "name": "Trustee Duties", "category": "flk2", "is_cross_cutting": False, "is_flk1_only": False, "active": True, "created_at": _now_dt},
            # Criminal
            {"code": "POLICE_POWERS", "name": "Police Powers", "category": "criminal", "is_cross_cutting": False, "is_flk1_only": False, "active": True, "created_at": _now_dt},
            {"code": "CRIM_EVIDENCE", "name": "Criminal Evidence", "category": "criminal", "is_cross_cutting": False, "is_flk1_only": False, "active": True, "created_at": _now_dt},
            {"code": "SENTENCING", "name": "Sentencing", "category": "criminal", "is_cross_cutting": False, "is_flk1_only": False, "active": True, "created_at": _now_dt},
        ],
    )

    # ------------------------------------------------------------------
    # 5. SEED DATA: Default assessment specification and blueprint
    # ------------------------------------------------------------------

    spec_table = sa.table(
        "assessment_specifications",
        sa.column("id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("effective_from", sa.Date),
        sa.column("effective_to", sa.Date),
        sa.column("source_url", sa.String),
        sa.column("description", sa.Text),
        sa.column("active", sa.Boolean),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )

    op.bulk_insert(
        spec_table,
        [
            {
                "name": "SQE1 Specification (2025–2026)",
                "effective_from": date(2025, 1, 1),
                "effective_to": None,
                "source_url": "https://sqe.sra.org.uk/assessments/sqe1-assessments/sqe1-specification",
                "description": (
                    "Default SQE1 specification seeded at install time. "
                    "Update this record when a new specification is published."
                ),
                "active": True,
                "created_at": _now_dt,
            }
        ],
    )

    # Retrieve the newly created spec id
    result = conn.execute(
        text("SELECT id FROM assessment_specifications WHERE active = 1 LIMIT 1")
    )
    spec_id = result.scalar()

    if spec_id is not None:
        # Create FLK1 and FLK2 blueprint profiles
        bp_table = sa.table(
            "blueprint_profiles",
            sa.column("id", sa.Integer),
            sa.column("specification_id", sa.Integer),
            sa.column("paper", sa.String),
            sa.column("name", sa.String),
            sa.column("strict_min_questions", sa.Integer),
            sa.column("cross_cutting_cap", sa.Numeric),
            sa.column("allow_option_randomise", sa.Boolean),
            sa.column("active", sa.Boolean),
            sa.column("created_at", sa.DateTime(timezone=True)),
        )

        op.bulk_insert(
            bp_table,
            [
                {
                    "specification_id": spec_id,
                    "paper": "FLK1",
                    "name": "FLK1 Blueprint 2025–2026",
                    "strict_min_questions": 30,
                    "cross_cutting_cap": "0.2000",
                    "allow_option_randomise": False,
                    "active": True,
                    "created_at": _now_dt,
                },
                {
                    "specification_id": spec_id,
                    "paper": "FLK2",
                    "name": "FLK2 Blueprint 2025–2026",
                    "strict_min_questions": 30,
                    "cross_cutting_cap": "0.2000",
                    "allow_option_randomise": False,
                    "active": True,
                    "created_at": _now_dt,
                },
            ],
        )

        # Fetch the profile IDs
        flk1_result = conn.execute(
            text(
                "SELECT id FROM blueprint_profiles WHERE specification_id = :sid AND paper = 'FLK1' LIMIT 1"
            ),
            {"sid": spec_id},
        )
        flk1_bp_id = flk1_result.scalar()

        flk2_result = conn.execute(
            text(
                "SELECT id FROM blueprint_profiles WHERE specification_id = :sid AND paper = 'FLK2' LIMIT 1"
            ),
            {"sid": spec_id},
        )
        flk2_bp_id = flk2_result.scalar()

        # Fetch subject IDs from what we just seeded
        def get_subject_id(code: str) -> int:
            r = conn.execute(
                text("SELECT id FROM subjects WHERE code = :code"), {"code": code}
            )
            return r.scalar()

        bs_table = sa.table(
            "blueprint_subjects",
            sa.column("profile_id", sa.Integer),
            sa.column("subject_id", sa.Integer),
            sa.column("min_pct", sa.Numeric),
            sa.column("max_pct", sa.Numeric),
            sa.column("target_pct", sa.Numeric),
        )

        if flk1_bp_id:
            # FLK1: 5 subjects at 14–20% (target 17%), Legal Services at 12–16% (target 15%)
            flk1_subjects = [
                ("FLK1_BLP", "0.1400", "0.2000", "0.1700"),
                ("FLK1_DR", "0.1400", "0.2000", "0.1700"),
                ("FLK1_CON", "0.1400", "0.2000", "0.1700"),
                ("FLK1_TORT", "0.1400", "0.2000", "0.1700"),
                ("FLK1_LS", "0.1400", "0.2000", "0.1700"),
                ("FLK1_LSVC", "0.1200", "0.1600", "0.1500"),
            ]
            op.bulk_insert(
                bs_table,
                [
                    {
                        "profile_id": flk1_bp_id,
                        "subject_id": get_subject_id(code),
                        "min_pct": min_p,
                        "max_pct": max_p,
                        "target_pct": tgt_p,
                    }
                    for code, min_p, max_p, tgt_p in flk1_subjects
                ],
            )

        if flk2_bp_id:
            # FLK2: 6 subjects each at 14–20%, target = 1/6 ≈ 0.1667
            flk2_subjects = [
                ("FLK2_PLP", "0.1400", "0.2000", "0.1667"),
                ("FLK2_LAND", "0.1400", "0.2000", "0.1667"),
                ("FLK2_WILLS", "0.1400", "0.2000", "0.1667"),
                ("FLK2_TRUST", "0.1400", "0.2000", "0.1667"),
                ("FLK2_CRIM", "0.1400", "0.2000", "0.1667"),
                ("FLK2_CRIMP", "0.1400", "0.2000", "0.1667"),
            ]
            op.bulk_insert(
                bs_table,
                [
                    {
                        "profile_id": flk2_bp_id,
                        "subject_id": get_subject_id(code),
                        "min_pct": min_p,
                        "max_pct": max_p,
                        "target_pct": tgt_p,
                    }
                    for code, min_p, max_p, tgt_p in flk2_subjects
                ],
            )


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------

def downgrade() -> None:
    # Drop new tables in reverse dependency order
    op.drop_table("audit_logs")
    op.drop_table("user_tag_stats")
    op.drop_table("user_subject_stats")
    op.drop_table("question_attempts")
    op.drop_table("user_answers")
    op.drop_table("test_session_options")
    op.drop_table("staged_options")
    op.drop_table("question_options")
    op.drop_table("question_tags")
    op.drop_table("blueprint_subjects")
    op.drop_table("blueprint_profiles")
    op.drop_table("exam_windows")
    op.drop_table("subjects")
    op.drop_table("tags")
    op.drop_table("assessment_specifications")

    # Revert added columns on existing tables (batch required for SQLite)
    with op.batch_alter_table("test_session_questions") as batch_op:
        for col in [
            "paper", "subject_id_snapshot", "subject_name_snapshot",
            "question_format", "section_number", "answer_e",
            "source_type_snapshot", "source_notice_snapshot",
        ]:
            batch_op.drop_column(col)
        batch_op.alter_column("answer_a", existing_type=sa.Text(), nullable=False)
        batch_op.alter_column("answer_b", existing_type=sa.Text(), nullable=False)
        batch_op.alter_column("answer_c", existing_type=sa.Text(), nullable=False)
        batch_op.alter_column("answer_d", existing_type=sa.Text(), nullable=False)
        batch_op.alter_column("correct_answer", existing_type=sa.String(1), nullable=False)

    with op.batch_alter_table("test_sessions") as batch_op:
        batch_op.alter_column(
            "mode", existing_type=sa.String(40), type_=sa.String(20), nullable=False
        )
        for col in [
            "paper", "blueprint_profile_id", "specification_id",
            "spec_version_snapshot", "blueprint_allocation_snapshot",
            "is_strict_blueprint", "section_count",
        ]:
            batch_op.drop_column(col)

    with op.batch_alter_table("staged_questions") as batch_op:
        for col in [
            "question_format", "paper", "subject_id", "subtopic", "logical_key",
            "answer_e", "explanation_source", "explanation_author", "explanation_independent",
            "source_type", "source_set", "source_question_id", "source_version",
            "source_url", "source_notice", "authority", "candidate_correct_pct",
            "law_cutoff_date", "valid_from", "valid_to", "last_reviewed",
            "reviewed_by", "review_status", "language", "notes",
        ]:
            batch_op.drop_column(col)
        batch_op.alter_column("answer_a", existing_type=sa.Text(), nullable=False)
        batch_op.alter_column("answer_b", existing_type=sa.Text(), nullable=False)
        batch_op.alter_column("answer_c", existing_type=sa.Text(), nullable=False)
        batch_op.alter_column("answer_d", existing_type=sa.Text(), nullable=False)
        batch_op.alter_column("correct_answer", existing_type=sa.String(1), nullable=False)

    with op.batch_alter_table("questions") as batch_op:
        for col in [
            "question_format", "paper", "subject_id", "subtopic", "logical_key",
            "answer_e", "explanation_source", "explanation_author", "explanation_independent",
            "source_type", "source_set", "source_question_id", "source_version",
            "source_url", "source_notice", "authority", "candidate_correct_pct",
            "law_cutoff_date", "valid_from", "valid_to", "last_reviewed",
            "reviewed_by", "review_status", "review_notes", "language", "notes",
        ]:
            batch_op.drop_column(col)
        batch_op.alter_column("answer_a", existing_type=sa.Text(), nullable=False)
        batch_op.alter_column("answer_b", existing_type=sa.Text(), nullable=False)
        batch_op.alter_column("answer_c", existing_type=sa.Text(), nullable=False)
        batch_op.alter_column("answer_d", existing_type=sa.Text(), nullable=False)
        batch_op.alter_column("correct_answer", existing_type=sa.String(1), nullable=False)

    with op.batch_alter_table("staged_imports") as batch_op:
        for col in ["detected_encoding", "flk1_count", "flk2_count", "specification_id"]:
            batch_op.drop_column(col)

    with op.batch_alter_table("question_bank_imports") as batch_op:
        for col in [
            "detected_encoding", "flk1_count", "flk2_count", "active_count",
            "inactive_count", "specification_id", "spec_version_snapshot", "import_notes",
        ]:
            batch_op.drop_column(col)
