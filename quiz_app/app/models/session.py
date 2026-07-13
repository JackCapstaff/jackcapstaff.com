"""Test session models: TestSession and TestSessionQuestion."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

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
    JSON,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db

if TYPE_CHECKING:
    pass  # User model imported at runtime via extensions


class TestSession(db.Model):
    """A user's test attempt (fresh, adaptive, or retest)."""

    __tablename__ = "test_sessions"
    __table_args__ = (
        Index("ix_test_sessions_user_status", "user_id", "status"),
        Index("ix_test_sessions_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mode: Mapped[str] = mapped_column(
        String(40), nullable=False, index=True
    )  # fresh | adaptive | sqe_blueprint | sqe_adaptive | sqe_simulation | retest
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="in_progress", index=True
    )  # in_progress | paused | submitted | expired
    question_count: Mapped[int] = mapped_column(Integer, nullable=False)
    selected_topics: Mapped[Optional[list]] = mapped_column(
        JSON, nullable=True
    )  # list of topic_keys (None = all topics)
    timed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    time_limit_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    paused_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    submitted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    submission_reason: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True
    )  # manual | time_expired
    random_seed: Mapped[int] = mapped_column(Integer, nullable=False)
    current_position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_session_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("test_sessions.id", ondelete="SET NULL"), nullable=True
    )
    adaptive_cold_start: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    adaptive_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    bank_import_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("question_bank_imports.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # SQE: which paper and blueprint profile this session was generated for
    paper: Mapped[Optional[str]] = mapped_column(String(4), nullable=True, index=True)
    blueprint_profile_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("blueprint_profiles.id", ondelete="SET NULL"), nullable=True
    )
    specification_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("assessment_specifications.id", ondelete="SET NULL"), nullable=True
    )
    # Snapshot of spec/blueprint version used at generation time
    spec_version_snapshot: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    blueprint_allocation_snapshot: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # is_strict_blueprint: was hard min/max enforced during selection?
    is_strict_blueprint: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # section_count: 1 for standard, 2 for full FLK (90+90)
    section_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    # Backref automatically creates User.test_sessions relationship
    user: Mapped["User"] = relationship("User", backref=db.backref('test_sessions', lazy='dynamic', cascade='all, delete-orphan'))
    questions: Mapped[list["TestSessionQuestion"]] = relationship(
        "TestSessionQuestion",
        back_populates="session",
        order_by="TestSessionQuestion.display_position",
        cascade="all, delete-orphan",
    )

    # ------------------------------------------------------------------
    # Computed helpers
    # ------------------------------------------------------------------

    @property
    def is_editable(self) -> bool:
        return self.status in ("in_progress", "paused")

    @property
    def is_complete(self) -> bool:
        return self.status in ("submitted", "expired")

    def is_expired_now(self) -> bool:
        if not self.timed or self.expires_at is None:
            return False
        return datetime.now(timezone.utc) >= self.expires_at

    def correct_count(self) -> int:
        return sum(1 for q in self.questions if q.is_correct is True)

    def incorrect_count(self) -> int:
        return sum(1 for q in self.questions if q.is_correct is False and not q.is_unanswered)

    def unanswered_count(self) -> int:
        return sum(1 for q in self.questions if q.is_unanswered)

    def percentage(self) -> float:
        if not self.question_count:
            return 0.0
        return self.correct_count() / self.question_count * 100

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TestSession id={self.id} user={self.user_id} status={self.status!r}>"


class TestSessionQuestion(db.Model):
    """
    A snapshot of one question within a test session.

    All question content is stored at the time the session is created so
    that historic results remain correct even after the question bank is
    replaced.
    """

    __tablename__ = "test_session_questions"
    __table_args__ = (
        UniqueConstraint("session_id", "display_position", name="uq_tsq_position"),
        UniqueConstraint("session_id", "external_question_id", name="uq_tsq_question"),
        Index("ix_tsq_session_position", "session_id", "display_position"),
        Index("ix_tsq_topic_key", "topic_key"),
        Index("ix_tsq_external_id", "external_question_id"),
        Index("ix_tsq_fingerprint", "content_fingerprint"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("test_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_question_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("questions.id", ondelete="SET NULL"), nullable=True
    )
    bank_import_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ---- Snapshot fields ----
    external_question_id: Mapped[str] = mapped_column(String(100), nullable=False)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    topic_key: Mapped[str] = mapped_column(String(255), nullable=False)
    # SQE classification snapshot
    paper: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)
    subject_id_snapshot: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    subject_name_snapshot: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    question_format: Mapped[str] = mapped_column(String(16), nullable=False, default="LEGACY_MCQ4")
    # Section within the test (1 or 2 for full FLK simulation)
    section_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    # Legacy flat answer columns — kept for backward compat; nullable for SQE5
    answer_a: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    answer_b: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    answer_c: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    answer_d: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    answer_e: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    correct_answer: Mapped[Optional[str]] = mapped_column(String(1), nullable=True)
    explanation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reference: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    difficulty: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    source_type_snapshot: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    source_notice_snapshot: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ---- Answer state ----
    display_position: Mapped[int] = mapped_column(Integer, nullable=False)
    selected_answer: Mapped[Optional[str]] = mapped_column(String(1), nullable=True)
    answered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    answer_change_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    time_spent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # ---- Scoring (null until finalized) ----
    is_correct: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    is_unanswered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    session: Mapped["TestSession"] = relationship(
        "TestSession", back_populates="questions"
    )
    snapshot_options: Mapped[list["TestSessionOption"]] = relationship(  # type: ignore[name-defined]
        "TestSessionOption",
        foreign_keys="TestSessionOption.test_session_question_id",
        cascade="all, delete-orphan",
        order_by="TestSessionOption.display_order",
    )

    @property
    def answer_text(self) -> Optional[str]:
        """Text of the user's selected answer (legacy flat-column support)."""
        mapping = {
            "A": self.answer_a,
            "B": self.answer_b,
            "C": self.answer_c,
            "D": self.answer_d,
            "E": self.answer_e,
        }
        return mapping.get(self.selected_answer) if self.selected_answer else None

    @property
    def correct_answer_text(self) -> str:
        mapping = {
            "A": self.answer_a,
            "B": self.answer_b,
            "C": self.answer_c,
            "D": self.answer_d,
            "E": self.answer_e,
        }
        return mapping.get(self.correct_answer or "", "") or ""

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<TSQuestion session={self.session_id} pos={self.display_position} "
            f"id={self.external_question_id!r}>"
        )
