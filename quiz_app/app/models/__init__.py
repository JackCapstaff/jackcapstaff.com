"""Models package — exposes all ORM classes."""
# Note: User is now provided by the main app via extensions.py
# Do not import from .user as that module has been removed
from .question import (
    QuestionBankImport,
    Question,
    StagedImport,
    StagedQuestion,
    FORMAT_SQE5,
    FORMAT_LEGACY_MCQ4,
    VALID_REVIEW_STATUSES,
    REVIEW_STATUS_OFFICIAL,
    REVIEW_STATUS_REVIEWED,
    REVIEW_STATUS_REVIEW_DUE,
    REVIEW_STATUS_DRAFT,
    REVIEW_STATUS_RETIRED,
)
from .session import TestSession, TestSessionQuestion
from .subject import Subject, Tag, question_tags
from .specification import (
    AssessmentSpecification,
    ExamWindow,
    BlueprintProfile,
    BlueprintSubject,
)
from .sqe import (
    QuestionOption,
    StagedOption,
    TestSessionOption,
    UserAnswer,
    QuestionAttempt,
    UserSubjectStat,
    UserTagStat,
    AuditLog,
)

__all__ = [
    # Question bank
    "QuestionBankImport",
    "Question",
    "StagedImport",
    "StagedQuestion",
    "FORMAT_SQE5",
    "FORMAT_LEGACY_MCQ4",
    "VALID_REVIEW_STATUSES",
    "REVIEW_STATUS_OFFICIAL",
    "REVIEW_STATUS_REVIEWED",
    "REVIEW_STATUS_REVIEW_DUE",
    "REVIEW_STATUS_DRAFT",
    "REVIEW_STATUS_RETIRED",
    # Sessions
    "TestSession",
    "TestSessionQuestion",
    # SQE structure
    "Subject",
    "Tag",
    "question_tags",
    "AssessmentSpecification",
    "ExamWindow",
    "BlueprintProfile",
    "BlueprintSubject",
    # SQE options / answers / stats
    "QuestionOption",
    "StagedOption",
    "TestSessionOption",
    "UserAnswer",
    "QuestionAttempt",
    "UserSubjectStat",
    "UserTagStat",
    "AuditLog",
]
