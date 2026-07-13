"""
CSV / TSV import service.

Responsibilities
----------------
* Parse uploaded bytes (UTF-8 ± BOM, comma or tab delimited)
* Validate every row against the spec
* Stage validated questions in StagedImport / StagedQuestion tables
* Confirm an import (atomic bank replacement)
* Cancel / expire staged imports
"""
from __future__ import annotations

import csv
import hashlib
import io
import secrets
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import urlparse

from flask import current_app

from ..extensions import db
from ..models.question import (
    Question,
    QuestionBankImport,
    StagedImport,
    StagedQuestion,
    _compute_fingerprint,
    normalize_topic_key,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REQUIRED_HEADERS = {
    "question id",
    "topic",
    "question",
    "answer a",
    "answer b",
    "answer c",
    "answer d",
    "correct answer",
}

OPTIONAL_HEADERS = {
    "explanation",
    "difficulty",
    "active",
    "image url",
    "reference",
    "last updated",
}

VALID_DIFFICULTIES = {"easy", "medium", "hard"}
TRUE_VALUES = {"true", "yes", "1", "t", "y"}
FALSE_VALUES = {"false", "no", "0", "f", "n"}
VALID_ANSWERS = {"A", "B", "C", "D"}
FORMULA_INJECTION_CHARS = {"=", "+", "-", "@"}


# ---------------------------------------------------------------------------
# Data transfer objects
# ---------------------------------------------------------------------------


@dataclass
class ValidationError:
    row: int
    field: str
    message: str


@dataclass
class ImportResult:
    token: str = ""
    filename: str = ""
    detected_delimiter: str = ","
    total_rows: int = 0
    valid_count: int = 0
    invalid_count: int = 0
    blank_count: int = 0
    topic_count: int = 0
    question_count: int = 0
    topics_summary: dict = field(default_factory=dict)  # topic -> count
    preview_rows: list = field(default_factory=list)
    errors: list[ValidationError] = field(default_factory=list)
    ignored_columns: list[str] = field(default_factory=list)
    has_errors: bool = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def stage_import(file_bytes: bytes, original_filename: str, user_id: int) -> ImportResult:
    """
    Parse, validate, and stage an uploaded question file.

    Returns an ImportResult.  If ImportResult.errors is non-empty the file
    was rejected and nothing was persisted.  On success, ImportResult.token
    can be passed to confirm_import().
    """
    result = ImportResult(filename=_safe_filename(original_filename))

    # Checksum
    checksum = hashlib.sha256(file_bytes).hexdigest()

    # Decode
    try:
        text = file_bytes.decode("utf-8-sig")  # strips BOM if present
    except UnicodeDecodeError:
        result.errors.append(
            ValidationError(row=0, field="file", message="File is not valid UTF-8.")
        )
        result.has_errors = True
        return result

    # Detect delimiter
    delimiter = _detect_delimiter(text)
    result.detected_delimiter = delimiter

    # Parse
    reader_stream = io.StringIO(text)
    try:
        reader = csv.DictReader(reader_stream, delimiter=delimiter)
        raw_fieldnames = reader.fieldnames
    except csv.Error as exc:
        result.errors.append(
            ValidationError(row=0, field="file", message=f"CSV parse error: {exc}")
        )
        result.has_errors = True
        return result

    if raw_fieldnames is None:
        result.errors.append(
            ValidationError(row=0, field="file", message="File appears to be empty.")
        )
        result.has_errors = True
        return result

    # Normalise headers
    normalised_headers = [h.strip().lower() for h in raw_fieldnames]

    # Duplicate header check
    if len(normalised_headers) != len(set(normalised_headers)):
        result.errors.append(
            ValidationError(row=0, field="headers", message="Duplicate column headers detected.")
        )
        result.has_errors = True
        return result

    # Required column check
    missing = REQUIRED_HEADERS - set(normalised_headers)
    if missing:
        result.errors.append(
            ValidationError(
                row=0,
                field="headers",
                message=f"Missing required columns: {', '.join(sorted(missing))}.",
            )
        )
        result.has_errors = True
        return result

    # Build header map: normalised -> original
    header_map = {norm: orig for norm, orig in zip(normalised_headers, raw_fieldnames)}
    known_headers = REQUIRED_HEADERS | OPTIONAL_HEADERS
    result.ignored_columns = [
        header_map[h] for h in normalised_headers if h not in known_headers
    ]

    # Validate rows
    valid_rows: list[dict] = []
    seen_ids: set[str] = set()
    row_num = 1  # 1-based (header is row 0)

    for raw_row in reader:
        row_num += 1

        # Remap to normalised keys
        row = {k.strip().lower(): (v.strip() if v else "") for k, v in raw_row.items() if k}

        # Skip blank rows
        if not any(row.values()):
            result.blank_count += 1
            continue

        result.total_rows += 1
        row_errors: list[ValidationError] = []

        # Required scalar fields
        ext_id = row.get("question id", "")
        if not ext_id:
            row_errors.append(
                ValidationError(row_num, "question id", "Question ID must not be blank.")
            )
        elif ext_id in seen_ids:
            row_errors.append(
                ValidationError(row_num, "question id", f"Duplicate Question ID '{ext_id}'.")
            )
        else:
            seen_ids.add(ext_id)

        topic = row.get("topic", "")
        if not topic:
            row_errors.append(ValidationError(row_num, "topic", "Topic must not be blank."))

        question_text = row.get("question", "")
        if not question_text:
            row_errors.append(ValidationError(row_num, "question", "Question text must not be blank."))

        for letter in ("a", "b", "c", "d"):
            if not row.get(f"answer {letter}", ""):
                row_errors.append(
                    ValidationError(
                        row_num, f"answer {letter}", f"Answer {letter.upper()} must not be blank."
                    )
                )

        correct = row.get("correct answer", "").upper()
        if correct not in VALID_ANSWERS:
            row_errors.append(
                ValidationError(
                    row_num,
                    "correct answer",
                    f"Correct answer must be A, B, C, or D (got '{correct}').",
                )
            )

        # Optional fields
        difficulty = row.get("difficulty", "")
        if difficulty and difficulty.lower() not in VALID_DIFFICULTIES:
            row_errors.append(
                ValidationError(
                    row_num,
                    "difficulty",
                    f"Difficulty must be Easy, Medium, or Hard (got '{difficulty}').",
                )
            )

        active_val = row.get("active", "")
        if active_val and active_val.lower() not in (TRUE_VALUES | FALSE_VALUES):
            row_errors.append(
                ValidationError(
                    row_num,
                    "active",
                    f"Active must be true/false/yes/no/1/0 (got '{active_val}').",
                )
            )

        image_url = row.get("image url", "")
        if image_url:
            try:
                parsed = urlparse(image_url)
                if parsed.scheme not in ("http", "https"):
                    raise ValueError("scheme")
            except Exception:
                row_errors.append(
                    ValidationError(
                        row_num, "image url", "Image URL must use http or https."
                    )
                )

        if row_errors:
            result.errors.extend(row_errors)
            result.invalid_count += 1
        else:
            # Parse active flag
            active_parsed = True
            if active_val:
                active_parsed = active_val.lower() in TRUE_VALUES

            valid_rows.append(
                {
                    "external_question_id": ext_id,
                    "topic": topic,
                    "topic_key": normalize_topic_key(topic),
                    "question_text": question_text,
                    "answer_a": row.get("answer a", ""),
                    "answer_b": row.get("answer b", ""),
                    "answer_c": row.get("answer c", ""),
                    "answer_d": row.get("answer d", ""),
                    "correct_answer": correct,
                    "explanation": row.get("explanation") or None,
                    "difficulty": difficulty.capitalize() if difficulty else None,
                    "active": active_parsed,
                    "image_url": image_url or None,
                    "reference": row.get("reference") or None,
                    "last_updated": row.get("last updated") or None,
                }
            )
            result.valid_count += 1

    if not result.errors and not valid_rows:
        result.errors.append(
            ValidationError(row=0, field="file", message="File contains no valid questions.")
        )

    if result.errors:
        result.has_errors = True
        return result

    # Build summary
    topic_counts: dict[str, int] = {}
    for vr in valid_rows:
        topic_counts[vr["topic"]] = topic_counts.get(vr["topic"], 0) + 1

    result.question_count = len(valid_rows)
    result.topic_count = len(topic_counts)
    result.topics_summary = topic_counts
    result.preview_rows = valid_rows[:10]

    # Persist staged import
    expiry_minutes = current_app.config.get("STAGED_IMPORT_EXPIRY_MINUTES", 60)
    token = secrets.token_hex(32)
    staged = StagedImport(
        user_id=user_id,
        token=token,
        filename=result.filename,
        checksum=checksum,
        detected_delimiter=delimiter,
        row_count=result.total_rows,
        question_count=result.question_count,
        topic_count=result.topic_count,
        status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=expiry_minutes),
        validation_summary={
            "total_rows": result.total_rows,
            "valid_count": result.valid_count,
            "invalid_count": result.invalid_count,
            "blank_count": result.blank_count,
            "topic_count": result.topic_count,
            "question_count": result.question_count,
            "ignored_columns": result.ignored_columns,
        },
    )
    db.session.add(staged)
    db.session.flush()  # get staged.id

    for vr in valid_rows:
        fingerprint = _compute_fingerprint(
            vr["external_question_id"],
            vr["question_text"],
            vr["answer_a"],
            vr["answer_b"],
            vr["answer_c"],
            vr["answer_d"],
            vr["correct_answer"],
        )
        sq = StagedQuestion(staged_import_id=staged.id, content_fingerprint=fingerprint, **vr)
        db.session.add(sq)

    db.session.commit()
    result.token = token
    return result


def confirm_import(token: str, user_id: int) -> QuestionBankImport:
    """
    Atomically replace the active question bank from a staged import.

    Raises ValueError if the token is invalid, expired, or not owned
    by user_id.  All DB work is done in a single transaction.
    """
    staged = db.session.execute(
        db.select(StagedImport).where(StagedImport.token == token)
    ).scalar_one_or_none()

    if staged is None:
        raise ValueError("Staged import not found.")
    if staged.user_id != user_id:
        raise ValueError("Access denied.")
    if staged.status != "pending":
        raise ValueError(f"Import already {staged.status}.")
    
    expires_at = staged.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    
    if datetime.now(timezone.utc) > expires_at:
        staged.status = "expired"
        db.session.commit()
        raise ValueError("Staged import has expired.")

    # Build QuestionBankImport record
    bank_import = QuestionBankImport(
        importer_user_id=user_id,
        filename=staged.filename,
        checksum=staged.checksum,
        detected_delimiter=staged.detected_delimiter,
        row_count=staged.row_count,
        question_count=staged.question_count,
        topic_count=staged.topic_count,
        active=False,  # will be set below, after deactivating old
        status="active",
        validation_summary=staged.validation_summary,
    )
    db.session.add(bank_import)
    db.session.flush()  # get bank_import.id

    # Insert new questions
    for sq in staged.staged_questions:
        q = Question(
            bank_import_id=bank_import.id,
            external_question_id=sq.external_question_id,
            topic=sq.topic,
            topic_key=sq.topic_key,
            question_text=sq.question_text,
            answer_a=sq.answer_a,
            answer_b=sq.answer_b,
            answer_c=sq.answer_c,
            answer_d=sq.answer_d,
            correct_answer=sq.correct_answer,
            explanation=sq.explanation,
            difficulty=sq.difficulty,
            active=sq.active,
            image_url=sq.image_url,
            reference=sq.reference,
            last_updated=sq.last_updated,
            content_fingerprint=sq.content_fingerprint,
        )
        db.session.add(q)

    # Deactivate all previous banks
    db.session.execute(
        db.update(QuestionBankImport)
        .where(QuestionBankImport.active == True)  # noqa: E712
        .values(active=False, status="superseded")
    )

    # Activate new bank
    bank_import.active = True
    staged.status = "confirmed"
    db.session.commit()
    return bank_import


def cancel_staged_import(token: str, user_id: int) -> None:
    """Cancel a pending staged import."""
    staged = db.session.execute(
        db.select(StagedImport).where(
            StagedImport.token == token, StagedImport.user_id == user_id
        )
    ).scalar_one_or_none()
    if staged and staged.status == "pending":
        staged.status = "cancelled"
        db.session.commit()


def get_active_bank_as_csv() -> str:
    """Export the active question bank as a sanitised CSV string."""
    active_bank = db.session.execute(
        db.select(QuestionBankImport).where(QuestionBankImport.active == True)  # noqa: E712
    ).scalar_one_or_none()

    if not active_bank:
        return ""

    questions = db.session.execute(
        db.select(Question).where(Question.bank_import_id == active_bank.id)
        .order_by(Question.topic_key, Question.external_question_id)
    ).scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Question ID",
            "Topic",
            "Question",
            "Answer A",
            "Answer B",
            "Answer C",
            "Answer D",
            "Correct Answer",
            "Explanation",
            "Difficulty",
            "Active",
            "Reference",
        ]
    )
    for q in questions:
        writer.writerow(
            [
                _sanitise_csv_cell(q.external_question_id),
                _sanitise_csv_cell(q.topic),
                _sanitise_csv_cell(q.question_text),
                _sanitise_csv_cell(q.answer_a),
                _sanitise_csv_cell(q.answer_b),
                _sanitise_csv_cell(q.answer_c),
                _sanitise_csv_cell(q.answer_d),
                q.correct_answer,
                _sanitise_csv_cell(q.explanation or ""),
                q.difficulty or "",
                "true" if q.active else "false",
                _sanitise_csv_cell(q.reference or ""),
            ]
        )
    return output.getvalue()


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _detect_delimiter(text: str) -> str:
    """Heuristic: count tabs vs commas in the first line."""
    first_line = text.split("\n", 1)[0]
    if first_line.count("\t") >= first_line.count(","):
        return "\t"
    return ","


def _safe_filename(name: str) -> str:
    """Strip path components and NUL bytes from an uploaded filename."""
    import os
    basename = os.path.basename(name.replace("\\", "/"))
    # Keep only printable ASCII
    safe = "".join(c for c in basename if ord(c) >= 32 and c not in '<>:"/\\|?*')
    return safe or "upload.csv"


def _sanitise_csv_cell(value: str) -> str:
    """Prefix formula-injection characters to prevent spreadsheet injection."""
    if value and value[0] in FORMULA_INJECTION_CHARS:
        return "'" + value
    return value
