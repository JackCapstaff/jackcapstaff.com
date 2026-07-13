"""
Quiz ORM models, returned as a dict from init_quiz_models(db).
All table names are prefixed with 'quiz_' to avoid collisions with
the existing jackcapstaff.com tables.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone


def normalize_topic_key(topic: str) -> str:
    return "_".join(topic.lower().split())


def compute_fingerprint(*parts: str) -> str:
    content = "|".join(p.strip() for p in parts)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def init_quiz_models(db):

    class QuizUser(db.Model):
        __tablename__ = "quiz_users"

        id = db.Column(db.Integer, primary_key=True)
        username = db.Column(db.String(80), unique=True, nullable=False, index=True)
        email = db.Column(db.String(255), unique=True, nullable=False, index=True)
        display_name = db.Column(db.String(120), nullable=False)
        password_hash = db.Column(db.String(256), nullable=False)
        role = db.Column(db.String(20), nullable=False, default="user", index=True)
        active = db.Column(db.Boolean, nullable=False, default=True)
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
        updated_at = db.Column(
            db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
        )

        def set_password(self, password):
            from werkzeug.security import generate_password_hash
            self.password_hash = generate_password_hash(password)

        def check_password(self, password):
            from werkzeug.security import check_password_hash
            return check_password_hash(self.password_hash, password)

        @property
        def is_admin(self):
            return self.role == "admin"

        def __repr__(self):
            return f"<QuizUser {self.username!r}>"

    class QuestionBankImport(db.Model):
        __tablename__ = "quiz_bank_imports"

        id = db.Column(db.Integer, primary_key=True)
        importer_user_id = db.Column(db.Integer, db.ForeignKey("quiz_users.id", ondelete="SET NULL"), nullable=True, index=True)
        filename = db.Column(db.String(255), nullable=False)
        checksum = db.Column(db.String(64), nullable=False)
        detected_delimiter = db.Column(db.String(4), nullable=False, default=",")
        row_count = db.Column(db.Integer, nullable=False, default=0)
        question_count = db.Column(db.Integer, nullable=False, default=0)
        topic_count = db.Column(db.Integer, nullable=False, default=0)
        active = db.Column(db.Boolean, nullable=False, default=False, index=True)
        status = db.Column(db.String(20), nullable=False, default="pending", index=True)
        imported_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
        validation_summary = db.Column(db.JSON, nullable=True)

        importer = db.relationship("QuizUser", backref=db.backref("quiz_imports", lazy=True))
        questions = db.relationship("Question", back_populates="bank_import", cascade="all, delete-orphan")

        def __repr__(self):
            return f"<QuestionBankImport id={self.id} active={self.active}>"

    class Question(db.Model):
        __tablename__ = "quiz_questions"
        __table_args__ = (
            db.UniqueConstraint("bank_import_id", "external_question_id", name="uq_quiz_q_per_bank"),
            db.Index("ix_quiz_q_topic_key", "topic_key"),
            db.Index("ix_quiz_q_bank_active", "bank_import_id", "active"),
        )

        id = db.Column(db.Integer, primary_key=True)
        bank_import_id = db.Column(db.Integer, db.ForeignKey("quiz_bank_imports.id", ondelete="CASCADE"), nullable=False, index=True)
        external_question_id = db.Column(db.String(100), nullable=False, index=True)
        topic = db.Column(db.String(255), nullable=False)
        topic_key = db.Column(db.String(255), nullable=False, index=True)
        question_text = db.Column(db.Text, nullable=False)
        answer_a = db.Column(db.Text, nullable=False)
        answer_b = db.Column(db.Text, nullable=False)
        answer_c = db.Column(db.Text, nullable=False)
        answer_d = db.Column(db.Text, nullable=False)
        correct_answer = db.Column(db.String(1), nullable=False)
        explanation = db.Column(db.Text, nullable=True)
        difficulty = db.Column(db.String(10), nullable=True)
        active = db.Column(db.Boolean, nullable=False, default=True, index=True)
        image_url = db.Column(db.String(2048), nullable=True)
        reference = db.Column(db.Text, nullable=True)
        last_updated = db.Column(db.String(20), nullable=True)
        content_fingerprint = db.Column(db.String(64), nullable=False, index=True)
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

        bank_import = db.relationship("QuestionBankImport", back_populates="questions")

        def __repr__(self):
            return f"<Question {self.external_question_id!r}>"

    class StagedImport(db.Model):
        __tablename__ = "quiz_staged_imports"

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey("quiz_users.id", ondelete="CASCADE"), nullable=False, index=True)
        token = db.Column(db.String(64), unique=True, nullable=False, index=True)
        filename = db.Column(db.String(255), nullable=False)
        checksum = db.Column(db.String(64), nullable=False)
        detected_delimiter = db.Column(db.String(4), nullable=False, default=",")
        row_count = db.Column(db.Integer, nullable=False, default=0)
        question_count = db.Column(db.Integer, nullable=False, default=0)
        topic_count = db.Column(db.Integer, nullable=False, default=0)
        status = db.Column(db.String(20), nullable=False, default="pending")
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
        expires_at = db.Column(db.DateTime, nullable=False, index=True)
        validation_summary = db.Column(db.JSON, nullable=True)

        user = db.relationship("QuizUser", backref=db.backref("staged_imports", lazy=True))
        staged_questions = db.relationship("StagedQuestion", back_populates="staged_import", cascade="all, delete-orphan")

    class StagedQuestion(db.Model):
        __tablename__ = "quiz_staged_questions"

        id = db.Column(db.Integer, primary_key=True)
        staged_import_id = db.Column(db.Integer, db.ForeignKey("quiz_staged_imports.id", ondelete="CASCADE"), nullable=False, index=True)
        external_question_id = db.Column(db.String(100), nullable=False)
        topic = db.Column(db.String(255), nullable=False)
        topic_key = db.Column(db.String(255), nullable=False)
        question_text = db.Column(db.Text, nullable=False)
        answer_a = db.Column(db.Text, nullable=False)
        answer_b = db.Column(db.Text, nullable=False)
        answer_c = db.Column(db.Text, nullable=False)
        answer_d = db.Column(db.Text, nullable=False)
        correct_answer = db.Column(db.String(1), nullable=False)
        explanation = db.Column(db.Text, nullable=True)
        difficulty = db.Column(db.String(10), nullable=True)
        active = db.Column(db.Boolean, nullable=False, default=True)
        image_url = db.Column(db.String(2048), nullable=True)
        reference = db.Column(db.Text, nullable=True)
        last_updated = db.Column(db.String(20), nullable=True)
        content_fingerprint = db.Column(db.String(64), nullable=False)

        staged_import = db.relationship("StagedImport", back_populates="staged_questions")

    class TestSession(db.Model):
        __tablename__ = "quiz_test_sessions"
        __table_args__ = (
            db.Index("ix_qts_user_status", "user_id", "status"),
            db.Index("ix_qts_user_created", "user_id", "created_at"),
        )

        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey("quiz_users.id", ondelete="CASCADE"), nullable=False, index=True)
        mode = db.Column(db.String(20), nullable=False, index=True)
        status = db.Column(db.String(20), nullable=False, default="in_progress", index=True)
        question_count = db.Column(db.Integer, nullable=False)
        selected_topics = db.Column(db.JSON, nullable=True)
        timed = db.Column(db.Boolean, nullable=False, default=False)
        time_limit_seconds = db.Column(db.Integer, nullable=True)
        started_at = db.Column(db.DateTime, nullable=True)
        expires_at = db.Column(db.DateTime, nullable=True, index=True)
        paused_at = db.Column(db.DateTime, nullable=True)
        submitted_at = db.Column(db.DateTime, nullable=True, index=True)
        submission_reason = db.Column(db.String(20), nullable=True)
        random_seed = db.Column(db.Integer, nullable=False)
        current_position = db.Column(db.Integer, nullable=False, default=0)
        source_session_id = db.Column(db.Integer, db.ForeignKey("quiz_test_sessions.id", ondelete="SET NULL"), nullable=True)
        adaptive_cold_start = db.Column(db.Boolean, nullable=False, default=False)
        adaptive_metadata = db.Column(db.JSON, nullable=True)
        bank_import_id = db.Column(db.Integer, db.ForeignKey("quiz_bank_imports.id", ondelete="SET NULL"), nullable=True, index=True)
        created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
        updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

        user = db.relationship("QuizUser", backref=db.backref("test_sessions", lazy="dynamic", cascade="all, delete-orphan"))
        questions = db.relationship(
            "TestSessionQuestion",
            back_populates="session",
            order_by="TestSessionQuestion.display_position",
            cascade="all, delete-orphan",
        )

        @property
        def is_editable(self):
            return self.status in ("in_progress", "paused")

        @property
        def is_complete(self):
            return self.status in ("submitted", "expired")

        def is_expired_now(self):
            if not self.timed or self.expires_at is None:
                return False
            return datetime.utcnow() >= self.expires_at

        def correct_count(self):
            return sum(1 for q in self.questions if q.is_correct is True)

        def incorrect_count(self):
            return sum(1 for q in self.questions if q.is_correct is False and not q.is_unanswered)

        def unanswered_count(self):
            return sum(1 for q in self.questions if q.is_unanswered)

        def percentage(self):
            if not self.question_count:
                return 0.0
            return self.correct_count() / self.question_count * 100

        def __repr__(self):
            return f"<TestSession id={self.id} user={self.user_id} status={self.status!r}>"

    class TestSessionQuestion(db.Model):
        __tablename__ = "quiz_session_questions"
        __table_args__ = (
            db.UniqueConstraint("session_id", "display_position", name="uq_qsq_position"),
            db.UniqueConstraint("session_id", "external_question_id", name="uq_qsq_question"),
            db.Index("ix_qsq_session_pos", "session_id", "display_position"),
            db.Index("ix_qsq_topic_key", "topic_key"),
            db.Index("ix_qsq_ext_id", "external_question_id"),
            db.Index("ix_qsq_fingerprint", "content_fingerprint"),
        )

        id = db.Column(db.Integer, primary_key=True)
        session_id = db.Column(db.Integer, db.ForeignKey("quiz_test_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
        source_question_id = db.Column(db.Integer, db.ForeignKey("quiz_questions.id", ondelete="SET NULL"), nullable=True)
        bank_import_id = db.Column(db.Integer, nullable=True)

        # Snapshot fields
        external_question_id = db.Column(db.String(100), nullable=False)
        content_fingerprint = db.Column(db.String(64), nullable=False)
        topic = db.Column(db.String(255), nullable=False)
        topic_key = db.Column(db.String(255), nullable=False)
        question_text = db.Column(db.Text, nullable=False)
        answer_a = db.Column(db.Text, nullable=False)
        answer_b = db.Column(db.Text, nullable=False)
        answer_c = db.Column(db.Text, nullable=False)
        answer_d = db.Column(db.Text, nullable=False)
        correct_answer = db.Column(db.String(1), nullable=False)
        explanation = db.Column(db.Text, nullable=True)
        reference = db.Column(db.Text, nullable=True)
        difficulty = db.Column(db.String(10), nullable=True)

        # Answer state
        display_position = db.Column(db.Integer, nullable=False)
        selected_answer = db.Column(db.String(1), nullable=True)
        answered_at = db.Column(db.DateTime, nullable=True)
        answer_change_count = db.Column(db.Integer, nullable=False, default=0)
        time_spent = db.Column(db.Float, nullable=True)

        # Scoring
        is_correct = db.Column(db.Boolean, nullable=True)
        is_unanswered = db.Column(db.Boolean, nullable=False, default=False)

        session = db.relationship("TestSession", back_populates="questions")

        @property
        def answer_text(self):
            m = {"A": self.answer_a, "B": self.answer_b, "C": self.answer_c, "D": self.answer_d}
            return m.get(self.selected_answer) if self.selected_answer else None

        @property
        def correct_answer_text(self):
            m = {"A": self.answer_a, "B": self.answer_b, "C": self.answer_c, "D": self.answer_d}
            return m.get(self.correct_answer, "")

        def __repr__(self):
            return f"<TSQ session={self.session_id} pos={self.display_position}>"

    return {
        "QuizUser": QuizUser,
        "QuestionBankImport": QuestionBankImport,
        "Question": Question,
        "StagedImport": StagedImport,
        "StagedQuestion": StagedQuestion,
        "TestSession": TestSession,
        "TestSessionQuestion": TestSessionQuestion,
    }
