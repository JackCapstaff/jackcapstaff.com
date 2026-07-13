"""User model."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from flask_login import UserMixin
from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db

if TYPE_CHECKING:
    from .question import QuestionBankImport, StagedImport
    from .session import TestSession


class User(UserMixin, db.Model):
    """Application user with role-based access."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(
        String(80), unique=True, nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="user", index=True
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    test_sessions: Mapped[list["TestSession"]] = relationship(
        "TestSession",
        back_populates="user",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    imports: Mapped[list["QuestionBankImport"]] = relationship(
        "QuestionBankImport", back_populates="importer"
    )
    staged_imports: Mapped[list["StagedImport"]] = relationship(
        "StagedImport", back_populates="user", cascade="all, delete-orphan"
    )

    # ------------------------------------------------------------------
    # Auth helpers
    # ------------------------------------------------------------------

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def get_id(self) -> str:
        return str(self.id)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.username!r}>"
