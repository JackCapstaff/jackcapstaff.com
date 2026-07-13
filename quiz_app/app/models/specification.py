"""SQE assessment specification, exam window, and blueprint models."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db


class AssessmentSpecification(db.Model):
    """A versioned SQE1 assessment specification (e.g. pre-Sep-2026, post-Sep-2026)."""

    __tablename__ = "assessment_specifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    effective_from: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
    effective_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    blueprint_profiles: Mapped[list["BlueprintProfile"]] = relationship(
        "BlueprintProfile", back_populates="specification"
    )
    exam_windows: Mapped[list["ExamWindow"]] = relationship(
        "ExamWindow", back_populates="specification"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AssessmentSpecification {self.name!r} active={self.active}>"


class ExamWindow(db.Model):
    """A specific SQE1 assessment sitting window with its law cut-off date."""

    __tablename__ = "exam_windows"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    first_assessment_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
    law_cutoff_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    specification_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("assessment_specifications.id", ondelete="SET NULL"), nullable=True, index=True
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    specification: Mapped[Optional["AssessmentSpecification"]] = relationship(
        "AssessmentSpecification", back_populates="exam_windows"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ExamWindow {self.name!r}>"


class BlueprintProfile(db.Model):
    """A blueprint allocation profile for one paper within one specification."""

    __tablename__ = "blueprint_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    specification_id: Mapped[int] = mapped_column(
        ForeignKey("assessment_specifications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    paper: Mapped[str] = mapped_column(String(4), nullable=False)  # FLK1 | FLK2
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # strict: enforce hard min/max bounds (for 90Q+ simulations)
    strict_min_questions: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    # cross_cutting_cap: max % of questions that may be Ethics/Conduct/ML combined
    cross_cutting_cap: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, default=Decimal("0.20")
    )
    allow_option_randomise: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    specification: Mapped["AssessmentSpecification"] = relationship(
        "AssessmentSpecification", back_populates="blueprint_profiles"
    )
    subjects: Mapped[list["BlueprintSubject"]] = relationship(
        "BlueprintSubject", back_populates="profile", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BlueprintProfile {self.name!r} {self.paper}>"


class BlueprintSubject(db.Model):
    """Per-subject allocation range and target within a blueprint profile."""

    __tablename__ = "blueprint_subjects"

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("blueprint_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Stored as decimal fractions (e.g. 0.17 for 17%)
    min_pct: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    max_pct: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    target_pct: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)

    profile: Mapped["BlueprintProfile"] = relationship(
        "BlueprintProfile", back_populates="subjects"
    )
    subject: Mapped["Subject"] = relationship("Subject", back_populates="blueprint_subjects")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BlueprintSubject profile={self.profile_id} subject={self.subject_id}>"
