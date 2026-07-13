"""Question bank models: QuestionBankImport, Question, StagedImport, StagedQuestion."""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    JSON,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db

if TYPE_CHECKING:
    pass  # User model imported at runtime via extensions


def _compute_fingerprint(*parts: str) -> str:
    """SHA-256 fingerprint of normalized question content fields."""
    content = "|".join(p.strip() for p in parts)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def normalize_topic_key(topic: str) -> str:
    """Lowercase, whitespace-collapsed, underscore-separated topic key."""
    return "_".join(topic.lower().split())


# Review status constants used across the app
REVIEW_STATUS_OFFICIAL = "Official Source"
REVIEW_STATUS_REVIEWED = "Legally Reviewed"
REVIEW_STATUS_REVIEW_DUE = "Review Due"
REVIEW_STATUS_DRAFT = "Draft"
REVIEW_STATUS_RETIRED = "Retired"
VALID_REVIEW_STATUSES = (
    REVIEW_STATUS_OFFICIAL,
    REVIEW_STATUS_REVIEWED,
    REVIEW_STATUS_REVIEW_DUE,
    REVIEW_STATUS_DRAFT,
    REVIEW_STATUS_RETIRED,
)

# Question format markers
FORMAT_SQE5 = "SQE5"           # 5-option SQE question with normalised options
FORMAT_LEGACY_MCQ4 = "LEGACY_MCQ4"  # 4-option question in flat columns


class QuestionBankImport(db.Model):
    """Metadata record for each question bank upload."""

    __tablename__ = "question_bank_imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    importer_user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True, index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    detected_delimiter: Mapped[str] = mapped_column(String(4), nullable=False, default=",")
    detected_encoding: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    question_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    topic_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    flk1_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    flk2_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inactive_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", index=True
    )  # pending | active | superseded | failed
    # SQE: which spec version this bank was imported against
    specification_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("assessment_specifications.id", ondelete="SET NULL"), nullable=True, index=True
    )
    spec_version_snapshot: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    import_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    validation_summary: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    importer: Mapped[Optional["User"]] = relationship("User")  # type: ignore[name-defined]
    questions: Mapped[list["Question"]] = relationship(
        "Question", back_populates="bank_import", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<QuestionBankImport id={self.id} active={self.active}>"


class Question(db.Model):
    """A single question belonging to a specific question bank import."""

    __tablename__ = "questions"
    __table_args__ = (
        UniqueConstraint("bank_import_id", "external_question_id", name="uq_question_per_bank"),
        Index("ix_questions_topic_key", "topic_key"),
        Index("ix_questions_bank_active", "bank_import_id", "active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    bank_import_id: Mapped[int] = mapped_column(
        ForeignKey("question_bank_imports.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_question_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # Format marker: SQE5 (normalised options) or LEGACY_MCQ4 (flat columns)
    question_format: Mapped[str] = mapped_column(
        String(16), nullable=False, default=FORMAT_LEGACY_MCQ4, index=True
    )

    # ---- SQE classification ----
    paper: Mapped[Optional[str]] = mapped_column(String(4), nullable=True, index=True)  # FLK1 | FLK2
    subject_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    subtopic: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Logical identity key: used to track a question across bank versions (source_type|source_set|source_id)
    logical_key: Mapped[Optional[str]] = mapped_column(String(300), nullable=True, index=True)

    # ---- Legacy flat topic (kept for legacy questions) ----
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    topic_key: Mapped[str] = mapped_column(String(255), nullable=False)

    question_text: Mapped[str] = mapped_column(Text, nullable=False)

    # ---- Legacy flat answer columns (kept for LEGACY_MCQ4 and backward compat) ----
    answer_a: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    answer_b: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    answer_c: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    answer_d: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # SQE adds answer_e; also nullable for legacy
    answer_e: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # For legacy: A-D; for SQE5 questions using flat columns: A-E; for normalised: None
    correct_answer: Mapped[Optional[str]] = mapped_column(String(1), nullable=True)

    # ---- Explanation ----
    explanation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    explanation_source: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    explanation_author: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # explanation_independent: True means rationale is NOT from SRA; must be shown to users
    explanation_independent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # ---- Source metadata ----
    source_type: Mapped[Optional[str]] = mapped_column(String(60), nullable=True, index=True)
    source_set: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    source_question_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    source_version: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    source_notice: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    authority: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    candidate_correct_pct: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True
    )

    # ---- Legal currency ----
    law_cutoff_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    valid_from: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    valid_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    last_reviewed: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    review_status: Mapped[str] = mapped_column(
        String(40), nullable=False, default=REVIEW_STATUS_DRAFT, index=True
    )
    review_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ---- Misc ----
    difficulty: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="en")
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    image_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    reference: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_updated: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    bank_import: Mapped["QuestionBankImport"] = relationship(
        "QuestionBankImport", back_populates="questions"
    )
    options: Mapped[list["QuestionOption"]] = relationship(  # type: ignore[name-defined]
        "QuestionOption", back_populates="question", cascade="all, delete-orphan",
        order_by="QuestionOption.source_order",
    )

    def compute_fingerprint(self) -> str:
        if self.question_format == FORMAT_SQE5 and self.options:
            option_texts = "|".join(
                o.option_text for o in sorted(self.options, key=lambda o: o.source_order)
            )
            correct_texts = "|".join(
                o.option_text for o in self.options if o.is_correct
            )
            return _compute_fingerprint(
                self.external_question_id,
                self.question_text,
                option_texts,
                correct_texts,
                self.paper or "",
                self.topic,
            )
        # Legacy: use flat columns
        return _compute_fingerprint(
            self.external_question_id,
            self.question_text,
            self.answer_a or "",
            self.answer_b or "",
            self.answer_c or "",
            self.answer_d or "",
            self.correct_answer or "",
        )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Question {self.external_question_id!r} bank={self.bank_import_id}>"


class StagedImport(db.Model):
    """Temporary staging record for an in-progress upload, before confirmation."""

    __tablename__ = "staged_imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    detected_delimiter: Mapped[str] = mapped_column(String(4), nullable=False, default=",")
    detected_encoding: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    question_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    topic_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    flk1_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    flk2_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    specification_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("assessment_specifications.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending | confirmed | cancelled | expired
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    validation_summary: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    user: Mapped["User"] = relationship("User")  # type: ignore[name-defined]
    staged_questions: Mapped[list["StagedQuestion"]] = relationship(
        "StagedQuestion", back_populates="staged_import", cascade="all, delete-orphan"
    )


class StagedQuestion(db.Model):
    """A validated question row awaiting import confirmation."""

    __tablename__ = "staged_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    staged_import_id: Mapped[int] = mapped_column(
        ForeignKey("staged_imports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    external_question_id: Mapped[str] = mapped_column(String(100), nullable=False)
    question_format: Mapped[str] = mapped_column(
        String(16), nullable=False, default=FORMAT_LEGACY_MCQ4
    )

    # SQE classification
    paper: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)
    subject_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True
    )
    subtopic: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    logical_key: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)

    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    topic_key: Mapped[str] = mapped_column(String(255), nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)

    # Flat answer columns (legacy + SQE5 fallback)
    answer_a: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    answer_b: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    answer_c: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    answer_d: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    answer_e: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    correct_answer: Mapped[Optional[str]] = mapped_column(String(1), nullable=True)

    explanation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    explanation_source: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    explanation_author: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    explanation_independent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    source_type: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    source_set: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    source_question_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    source_version: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    source_notice: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    authority: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    candidate_correct_pct: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2), nullable=True
    )

    law_cutoff_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    valid_from: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    valid_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    last_reviewed: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    review_status: Mapped[str] = mapped_column(
        String(40), nullable=False, default=REVIEW_STATUS_DRAFT
    )

    difficulty: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="en")
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    image_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    reference: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_updated: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    staged_import: Mapped["StagedImport"] = relationship(
        "StagedImport", back_populates="staged_questions"
    )
    options: Mapped[list["StagedOption"]] = relationship(  # type: ignore[name-defined]
        "StagedOption", back_populates="staged_question", cascade="all, delete-orphan",
        order_by="StagedOption.source_order",
    )
