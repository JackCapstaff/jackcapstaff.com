"""Question bank models: QuestionBankImport, Question, StagedImport, StagedQuestion."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    JSON,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db

if TYPE_CHECKING:
    from .user import User
    from .session import TestSessionQuestion


def _compute_fingerprint(*parts: str) -> str:
    """SHA-256 fingerprint of normalized question content fields."""
    content = "|".join(p.strip() for p in parts)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def normalize_topic_key(topic: str) -> str:
    """Lowercase, whitespace-collapsed, underscore-separated topic key."""
    return "_".join(topic.lower().split())


class QuestionBankImport(db.Model):
    """Metadata record for each question bank upload."""

    __tablename__ = "question_bank_imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    importer_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    detected_delimiter: Mapped[str] = mapped_column(String(4), nullable=False, default=",")
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    question_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    topic_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", index=True
    )  # pending | active | superseded | failed
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    validation_summary: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    importer: Mapped[Optional["User"]] = relationship("User", back_populates="imports")
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
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    topic_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    answer_a: Mapped[str] = mapped_column(Text, nullable=False)
    answer_b: Mapped[str] = mapped_column(Text, nullable=False)
    answer_c: Mapped[str] = mapped_column(Text, nullable=False)
    answer_d: Mapped[str] = mapped_column(Text, nullable=False)
    correct_answer: Mapped[str] = mapped_column(String(1), nullable=False)
    explanation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    difficulty: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
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

    def compute_fingerprint(self) -> str:
        return _compute_fingerprint(
            self.external_question_id,
            self.question_text,
            self.answer_a,
            self.answer_b,
            self.answer_c,
            self.answer_d,
            self.correct_answer,
        )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Question {self.external_question_id!r} bank={self.bank_import_id}>"


class StagedImport(db.Model):
    """Temporary staging record for an in-progress upload, before confirmation."""

    __tablename__ = "staged_imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    detected_delimiter: Mapped[str] = mapped_column(String(4), nullable=False, default=",")
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    question_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    topic_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
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

    user: Mapped["User"] = relationship("User", back_populates="staged_imports")
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
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    topic_key: Mapped[str] = mapped_column(String(255), nullable=False)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    answer_a: Mapped[str] = mapped_column(Text, nullable=False)
    answer_b: Mapped[str] = mapped_column(Text, nullable=False)
    answer_c: Mapped[str] = mapped_column(Text, nullable=False)
    answer_d: Mapped[str] = mapped_column(Text, nullable=False)
    correct_answer: Mapped[str] = mapped_column(String(1), nullable=False)
    explanation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    difficulty: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    image_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    reference: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_updated: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    staged_import: Mapped["StagedImport"] = relationship(
        "StagedImport", back_populates="staged_questions"
    )
