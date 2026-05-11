from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    JSON,
    BigInteger,
    Index,
    ForeignKey,
)
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from db import Base


class Schedule(Base):
    __tablename__ = "schedules"
    id = Column(String, primary_key=True)
    ensemble_id = Column(String, index=True, nullable=False)
    name = Column(String, nullable=False)
    status = Column(String, default="draft")
    created_by = Column(String, nullable=True)
    created_at = Column(BigInteger, default=lambda: int(func.extract('epoch', func.now())))
    updated_at = Column(BigInteger, default=lambda: int(func.extract('epoch', func.now())), onupdate=lambda: int(func.extract('epoch', func.now())))
    G = Column(Integer, default=5)
    generated_at = Column(BigInteger, nullable=True)
    published_at = Column(BigInteger, nullable=True)
    followup_notifications = Column(JSON, default={})
    meta = Column(JSON, default={})


class Rehearsal(Base):
    __tablename__ = "rehearsals"
    id = Column(Integer, primary_key=True)
    schedule_id = Column(String, ForeignKey('schedules.id', ondelete='CASCADE'), index=True)
    rehearsal_num = Column(Integer, index=True)
    date = Column(String)
    start_time = Column(String)
    end_time = Column(String)
    break_minutes = Column(Integer)
    section = Column(String)
    event_type = Column(String)
    raw = Column(JSON)


class Attendance(Base):
    __tablename__ = "attendance"
    id = Column(Integer, primary_key=True)
    schedule_id = Column(String, ForeignKey('schedules.id', ondelete='CASCADE'), index=True)
    rehearsal_num = Column(Integer, index=True)
    user_id = Column(String, index=True)
    status = Column(String)  # yes|no|maybe
    note = Column(Text)
    responded_at = Column(BigInteger)

    __table_args__ = (
        Index('ix_attendance_schedule_rehearsal_user', 'schedule_id', 'rehearsal_num', 'user_id', unique=False),
    )


class TimedItem(Base):
    __tablename__ = "timed_items"
    id = Column(Integer, primary_key=True)
    schedule_id = Column(String, ForeignKey('schedules.id', ondelete='CASCADE'), index=True)
    rehearsal_num = Column(Integer, index=True)
    ordering = Column(Integer, nullable=True)
    work_id = Column(String, nullable=True)
    title = Column(String)
    start = Column(String)
    end = Column(String)
    meta = Column(JSON)


class AuditEntry(Base):
    __tablename__ = "audit_entries"
    id = Column(Integer, primary_key=True)
    schedule_id = Column(String, ForeignKey('schedules.id', ondelete='CASCADE'), index=True)
    ts = Column(BigInteger, default=lambda: int(func.extract('epoch', func.now())))
    action = Column(String)
    description = Column(Text)
    actor_id = Column(String)
    actor_email = Column(String)
    actor_name = Column(String)
    meta = Column(JSON)


# relationships (optional conveniences)
Schedule.rehearsals = relationship('Rehearsal', backref='schedule', cascade='all, delete-orphan')
Schedule.timed_items = relationship('TimedItem', backref='schedule', cascade='all, delete-orphan')
Schedule.audit_entries = relationship('AuditEntry', backref='schedule', cascade='all, delete-orphan')
