"""SQE canonical subject and tag models."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, String, Table, Column, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db


# Association table: Question ↔ Tag (many-to-many)
question_tags = Table(
    "question_tags",
    db.metadata,
    Column("question_id", ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Subject(db.Model):
    """Canonical SQE1 primary subject (FLK1 or FLK2)."""

    __tablename__ = "subjects"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)
    paper: Mapped[str] = mapped_column(String(4), nullable=False, index=True)  # FLK1 | FLK2
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    short_name: Mapped[str] = mapped_column(String(80), nullable=False)
    display_order: Mapped[int] = mapped_column(nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    blueprint_subjects: Mapped[list["BlueprintSubject"]] = relationship(
        "BlueprintSubject", back_populates="subject"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Subject {self.code!r} {self.paper}>"


class Tag(db.Model):
    """Cross-cutting secondary tag (e.g. Ethics, Money Laundering)."""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(60), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    # is_cross_cutting: Ethics, Professional Conduct, Money Laundering count towards the 20% cap
    is_cross_cutting: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # is_flk1_only: Money Laundering may only appear in FLK1
    is_flk1_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Tag {self.code!r}>"
