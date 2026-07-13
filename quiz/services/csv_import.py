"""
CSV / TSV import service for the quiz package.
Accepts raw bytes, validates rows, stages into DB, and confirms (atomic replace).
"""
from __future__ import annotations

import csv
import hashlib
import io
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlparse

REQUIRED_HEADERS = {
    "question id", "topic", "question",
    "answer a", "answer b", "answer c", "answer d",
    "correct answer",
}
OPTIONAL_HEADERS = {"explanation", "difficulty", "active", "image url", "reference", "last updated"}
VALID_DIFFICULTIES = {"easy", "medium", "hard"}
TRUE_VALUES = {"true", "yes", "1", "t", "y"}
FALSE_VALUES = {"false", "no", "0", "f", "n"}
VALID_ANSWERS = {"A", "B", "C", "D"}
FORMULA_INJECTION_CHARS = {"=", "+", "-", "@"}


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
    topics_summary: dict = field(default_factory=dict)
    preview_rows: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    ignored_columns: list = field(default_factory=list)
    has_errors: bool = False


def stage_import(file_bytes: bytes, original_filename: str, user_id: int, config: dict) -> ImportResult:
    """Parse, validate, and stage an uploaded question file."""
    from flask import current_app

    result = ImportResult(filename=_safe_filename(original_filename))
    checksum = hashlib.sha256(file_bytes).hexdigest()

    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        result.errors.append(ValidationError(0, "file", "File is not valid UTF-8."))
        result.has_errors = True
        return result

    delimiter = _detect_delimiter(text)
    result.detected_delimiter = delimiter

    try:
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        raw_fieldnames = reader.fieldnames
    except csv.Error as exc:
        result.errors.append(ValidationError(0, "file", f"CSV parse error: {exc}"))
        result.has_errors = True
        return result

    if not raw_fieldnames:
        result.errors.append(ValidationError(0, "file", "File appears to be empty."))
        result.has_errors = True
        return result

    normalised_headers = [h.strip().lower() for h in raw_fieldnames]

    if len(normalised_headers) != len(set(normalised_headers)):
        result.errors.append(ValidationError(0, "headers", "Duplicate column headers detected."))
        result.has_errors = True
        return result

    missing = REQUIRED_HEADERS - set(normalised_headers)
    if missing:
        result.errors.append(
            ValidationError(0, "headers", f"Missing required columns: {', '.join(sorted(missing))}.")
        )
        result.has_errors = True
        return result

    header_map = dict(zip(normalised_headers, raw_fieldnames))
    known = REQUIRED_HEADERS | OPTIONAL_HEADERS
    result.ignored_columns = [header_map[h] for h in normalised_headers if h not in known]

    valid_rows = []
    seen_ids: set = set()
    row_num = 1

    for raw_row in reader:
        row_num += 1
        row = {k.strip().lower(): (v.strip() if v else "") for k, v in raw_row.items() if k}

        if not any(row.values()):
            result.blank_count += 1
            continue

        result.total_rows += 1
        row_errors = []

        ext_id = row.get("question id", "")
        if not ext_id:
            row_errors.append(ValidationError(row_num, "question id", "Question ID must not be blank."))
        elif ext_id in seen_ids:
            row_errors.append(ValidationError(row_num, "question id", f"Duplicate Question ID '{ext_id}'."))
        else:
            seen_ids.add(ext_id)

        if not row.get("topic", ""):
            row_errors.append(ValidationError(row_num, "topic", "Topic must not be blank."))
        if not row.get("question", ""):
            row_errors.append(ValidationError(row_num, "question", "Question text must not be blank."))

        for letter in ("a", "b", "c", "d"):
            if not row.get(f"answer {letter}", ""):
                row_errors.append(ValidationError(row_num, f"answer {letter}", f"Answer {letter.upper()} must not be blank."))

        correct = row.get("correct answer", "").upper()
        if correct not in VALID_ANSWERS:
            row_errors.append(ValidationError(row_num, "correct answer", f"Correct answer must be A, B, C, or D (got '{correct}')."))

        difficulty = row.get("difficulty", "")
        if difficulty and difficulty.lower() not in VALID_DIFFICULTIES:
            row_errors.append(ValidationError(row_num, "difficulty", f"Difficulty must be Easy, Medium, or Hard (got '{difficulty}')."))

        active_val = row.get("active", "")
        if active_val and active_val.lower() not in (TRUE_VALUES | FALSE_VALUES):
            row_errors.append(ValidationError(row_num, "active", f"Active must be true/false/yes/no/1/0."))

        image_url = row.get("image url", "")
        if image_url:
            try:
                parsed = urlparse(image_url)
                if parsed.scheme not in ("http", "https"):
                    raise ValueError
            except Exception:
                row_errors.append(ValidationError(row_num, "image url", "Image URL must use http or https."))

        if row_errors:
            result.errors.extend(row_errors)
            result.invalid_count += 1
        else:
            active_parsed = True if not active_val else active_val.lower() in TRUE_VALUES
            from ..models import normalize_topic_key, compute_fingerprint
            topic = row.get("topic", "")
            valid_rows.append({
                "external_question_id": ext_id,
                "topic": topic,
                "topic_key": normalize_topic_key(topic),
                "question_text": row.get("question", ""),
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
                "content_fingerprint": compute_fingerprint(
                    ext_id, row.get("question", ""), row.get("answer a", ""),
                    row.get("answer b", ""), row.get("answer c", ""), row.get("answer d", ""), correct
                ),
            })
            result.valid_count += 1

    if not result.errors and not valid_rows:
        result.errors.append(ValidationError(0, "file", "File contains no valid questions."))

    if result.errors:
        result.has_errors = True
        return result

    topic_counts: dict = {}
    for vr in valid_rows:
        topic_counts[vr["topic"]] = topic_counts.get(vr["topic"], 0) + 1

    result.question_count = len(valid_rows)
    result.topic_count = len(topic_counts)
    result.topics_summary = topic_counts
    result.preview_rows = valid_rows[:10]

    expiry_minutes = config.get("QUIZ_STAGED_IMPORT_EXPIRY_MINUTES", 60)
    token = secrets.token_hex(32)

    db = current_app.db
    StagedImport = current_app.StagedImport
    StagedQuestion = current_app.StagedQuestion

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
        expires_at=datetime.utcnow() + timedelta(minutes=expiry_minutes),
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
    db.session.flush()

    for vr in valid_rows:
        sq = StagedQuestion(staged_import_id=staged.id, **vr)
        db.session.add(sq)

    db.session.commit()
    result.token = token
    return result


def confirm_import(token: str, user_id: int, config: dict):
    """Atomically replace the active question bank from a staged import."""
    from flask import current_app
    db = current_app.db
    StagedImport = current_app.StagedImport
    QuestionBankImport = current_app.QuestionBankImport
    Question = current_app.Question

    staged = StagedImport.query.filter_by(token=token).first()
    if staged is None:
        raise ValueError("Staged import not found.")
    if staged.user_id != user_id:
        raise ValueError("Access denied.")
    if staged.status != "pending":
        raise ValueError(f"Import already {staged.status}.")
    if datetime.utcnow() > staged.expires_at:
        staged.status = "expired"
        db.session.commit()
        raise ValueError("Staged import has expired.")

    bank_import = QuestionBankImport(
        importer_user_id=user_id,
        filename=staged.filename,
        checksum=staged.checksum,
        detected_delimiter=staged.detected_delimiter,
        row_count=staged.row_count,
        question_count=staged.question_count,
        topic_count=staged.topic_count,
        active=False,
        status="active",
        validation_summary=staged.validation_summary,
    )
    db.session.add(bank_import)
    db.session.flush()

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
    QuestionBankImport.query.filter_by(active=True).update(
        {"active": False, "status": "superseded"}
    )

    bank_import.active = True
    staged.status = "confirmed"
    db.session.commit()
    return bank_import


def cancel_staged_import(token: str, user_id: int):
    from flask import current_app
    db = current_app.db
    StagedImport = current_app.StagedImport
    staged = StagedImport.query.filter_by(token=token, user_id=user_id).first()
    if staged and staged.status == "pending":
        staged.status = "cancelled"
        db.session.commit()


def get_active_bank_as_csv() -> str:
    from flask import current_app
    db = current_app.db
    QuestionBankImport = current_app.QuestionBankImport
    Question = current_app.Question

    active_bank = QuestionBankImport.query.filter_by(active=True).first()
    if not active_bank:
        return ""

    questions = Question.query.filter_by(bank_import_id=active_bank.id).order_by(
        Question.topic_key, Question.external_question_id
    ).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Question ID", "Topic", "Question", "Answer A", "Answer B", "Answer C", "Answer D",
                     "Correct Answer", "Explanation", "Difficulty", "Active", "Reference"])
    for q in questions:
        writer.writerow([
            _sanitise_cell(q.external_question_id),
            _sanitise_cell(q.topic),
            _sanitise_cell(q.question_text),
            _sanitise_cell(q.answer_a),
            _sanitise_cell(q.answer_b),
            _sanitise_cell(q.answer_c),
            _sanitise_cell(q.answer_d),
            q.correct_answer,
            _sanitise_cell(q.explanation or ""),
            q.difficulty or "",
            "true" if q.active else "false",
            _sanitise_cell(q.reference or ""),
        ])
    return output.getvalue()


def _detect_delimiter(text: str) -> str:
    first = text.split("\n", 1)[0]
    return "\t" if first.count("\t") >= first.count(",") else ","


def _safe_filename(name: str) -> str:
    import os
    base = os.path.basename(name.replace("\\", "/"))
    safe = "".join(c for c in base if ord(c) >= 32 and c not in '<>:"/\\|?*')
    return safe or "upload.csv"


def _sanitise_cell(value: str) -> str:
    if value and value[0] in FORMULA_INJECTION_CHARS:
        return "'" + value
    return value
