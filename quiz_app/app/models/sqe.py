"""SQE normalised answer options and per-question attempt tracking."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db


class QuestionOption(db.Model):
    """A single answer option for an SQE question (normalised, stable ID)."""

    __tablename__ = "question_options"

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # source_label: the letter as it appeared in the source CSV (A–E)
    source_label: Mapped[str] = mapped_column(String(1), nullable=False)
    option_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_order: Mapped[int] = mapped_column(Integer, nullable=False)

    question: Mapped["Question"] = relationship("Question", back_populates="options")  # type: ignore[name-defined]

    def __repr__(self) -> str:  # pragma: no cover
        return f"<QuestionOption {self.source_label} q={self.question_id} correct={self.is_correct}>"


class StagedOption(db.Model):
    """Staged answer option awaiting import confirmation."""

    __tablename__ = "staged_options"

    id: Mapped[int] = mapped_column(primary_key=True)
    staged_question_id: Mapped[int] = mapped_column(
        ForeignKey("staged_questions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_label: Mapped[str] = mapped_column(String(1), nullable=False)
    option_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_order: Mapped[int] = mapped_column(Integer, nullable=False)

    staged_question: Mapped["StagedQuestion"] = relationship(  # type: ignore[name-defined]
        "StagedQuestion", back_populates="options"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<StagedOption {self.source_label} sq={self.staged_question_id}>"


class TestSessionOption(db.Model):
    """Snapshot of a single displayed option within a test-session question."""

    __tablename__ = "test_session_options"

    id: Mapped[int] = mapped_column(primary_key=True)
    test_session_question_id: Mapped[int] = mapped_column(
        ForeignKey("test_session_questions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Link back to original option for audit; nullable because legacy questions won't have one
    original_option_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("question_options.id", ondelete="SET NULL"), nullable=True
    )
    display_label: Mapped[str] = mapped_column(String(1), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)
    option_text_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    # is_correct is NOT sent to the browser — only used server-side for scoring
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<TSOption {self.display_label} tsq={self.test_session_question_id}>"
        )


class UserAnswer(db.Model):
    """The user's answer record for one question in a session (separate from snapshot)."""

    __tablename__ = "user_answers"

    # Primary key is the test_session_question_id (one answer row per question per session)
    test_session_question_id: Mapped[int] = mapped_column(
        ForeignKey("test_session_questions.id", ondelete="CASCADE"), primary_key=True
    )
    # selected_session_option_id references the TestSessionOption chosen
    selected_session_option_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("test_session_options.id", ondelete="SET NULL"), nullable=True
    )
    selected_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    change_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # is_correct: populated server-side after submission; NULL while test is in progress
    is_correct: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<UserAnswer tsq={self.test_session_question_id} correct={self.is_correct}>"


class QuestionAttempt(db.Model):
    """Per-user, per-question attempt record for adaptive history."""

    __tablename__ = "question_attempts"
    __table_args__ = (
        Index("ix_qa_user_key", "user_id", "logical_question_key"),
        Index("ix_qa_user_subject", "user_id", "subject_id"),
        Index("ix_qa_attempted", "attempted_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # logical_question_key: source_type|source_set|source_question_id (stable across versions)
    logical_question_key: Mapped[str] = mapped_column(String(200), nullable=False)
    question_version_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("questions.id", ondelete="SET NULL"), nullable=True
    )
    test_session_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("test_sessions.id", ondelete="SET NULL"), nullable=True
    )
    subject_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    # result: correct | incorrect | unanswered
    result: Mapped[str] = mapped_column(String(12), nullable=False)
    response_time_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Snapshots at time of attempt for trend analysis
    consecutive_correct_snapshot: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consecutive_incorrect_snapshot: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<QuestionAttempt user={self.user_id} key={self.logical_question_key!r}>"


class UserSubjectStat(db.Model):
    """Aggregated per-user per-subject performance stats (cache for adaptive weighting)."""

    __tablename__ = "user_subject_stats"
    __table_args__ = (
        UniqueConstraint("user_id", "subject_id", name="uq_uss_user_subject"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # recent_* uses a rolling window (configurable, default last 50 attempts)
    recent_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recent_correct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_attempted: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    subject: Mapped["Subject"] = relationship("Subject")  # type: ignore[name-defined]

    def __repr__(self) -> str:  # pragma: no cover
        return f"<UserSubjectStat user={self.user_id} subject={self.subject_id}>"


class UserTagStat(db.Model):
    """Aggregated per-user per-tag performance stats."""

    __tablename__ = "user_tag_stats"
    __table_args__ = (
        UniqueConstraint("user_id", "tag_id", name="uq_uts_user_tag"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recent_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recent_correct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_attempted: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    tag: Mapped["Tag"] = relationship("Tag")  # type: ignore[name-defined]

    def __repr__(self) -> str:  # pragma: no cover
        return f"<UserTagStat user={self.user_id} tag={self.tag_id}>"


class AuditLog(db.Model):
    """Administrator and system audit trail."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_actor", "actor_user_id"),
        Index("ix_audit_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_type: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    entity_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    before_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    after_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AuditLog {self.action!r} entity={self.entity_type}:{self.entity_id}>"
