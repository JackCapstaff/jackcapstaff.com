"""
CSV / TSV import service — SQE1 edition.

Responsibilities
----------------
* Parse uploaded bytes (UTF-8 ± BOM, comma or tab delimited)
* Auto-detect SQE format (Paper/Primary Topic/Answer E) vs legacy (Topic/Answer A-D)
* Validate every row against the SQE or legacy spec
* Stage validated questions + options in Staged* tables
* Confirm an import (atomic bank replacement)
* Cancel / expire staged imports
* Export active bank to extended SQE CSV

SQE format required columns
----------------------------
Question ID, Paper, Primary Topic, Question,
Answer A, Answer B, Answer C, Answer D, Answer E, Correct Answer

Legacy format required columns (backward compat)
-------------------------------------------------
Question ID, Topic, Question, Answer A, Answer B, Answer C, Answer D, Correct Answer
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import secrets
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timezone, timedelta
from typing import Optional
from urllib.parse import urlparse

from flask import current_app

from ..extensions import db
from ..models.question import (
    Question,
    QuestionBankImport,
    StagedImport,
    StagedQuestion,
    FORMAT_SQE5,
    FORMAT_LEGACY_MCQ4,
    VALID_REVIEW_STATUSES,
    REVIEW_STATUS_DRAFT,
    REVIEW_STATUS_OFFICIAL,
    _compute_fingerprint,
    normalize_topic_key,
)
from ..models.sqe import QuestionOption, StagedOption
from ..models.subject import Subject, Tag

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# SQE required headers (normalised lowercase)
SQE_REQUIRED_HEADERS = {
    "question id",
    "paper",
    "primary topic",
    "question",
    "answer a",
    "answer b",
    "answer c",
    "answer d",
    "answer e",
    "correct answer",
}

# Legacy required headers
LEGACY_REQUIRED_HEADERS = {
    "question id",
    "topic",
    "question",
    "answer a",
    "answer b",
    "answer c",
    "answer d",
    "correct answer",
}

# Optional headers (both formats)
SQE_OPTIONAL_HEADERS = {
    "secondary tags",
    "subtopic",
    "explanation",
    "explanation source",
    "explanation author",
    "authority",
    "source",
    "source type",
    "source set",
    "source question id",
    "source version",
    "source url",
    "source notice",
    "candidate correct percentage",
    "difficulty",
    "law cut-off date",
    "valid from",
    "valid to",
    "last reviewed",
    "reviewed by",
    "review status",
    "active",
    "language",
    "notes",
    "image url",
    "reference",
    "last updated",
}

VALID_PAPERS = {"FLK1", "FLK2"}
VALID_ANSWERS_SQE = {"A", "B", "C", "D", "E"}
VALID_ANSWERS_LEGACY = {"A", "B", "C", "D"}
VALID_DIFFICULTIES = {"easy", "medium", "hard"}
TRUE_VALUES = {"true", "yes", "1", "t", "y"}
FALSE_VALUES = {"false", "no", "0", "f", "n"}
FORMULA_INJECTION_CHARS = {"=", "+", "-", "@"}
TAG_SEPARATOR = "|"

ISO_DATE_PATTERNS = [
    re.compile(r"^\d{4}-\d{2}-\d{2}$"),     # YYYY-MM-DD
    re.compile(r"^\d{2}/\d{2}/\d{4}$"),      # DD/MM/YYYY
    re.compile(r"^\d{4}/\d{2}/\d{2}$"),      # YYYY/MM/DD
]


# ---------------------------------------------------------------------------
# Data transfer objects
# ---------------------------------------------------------------------------


@dataclass
class ValidationError:
    row: int
    field: str
    message: str
    severity: str = "error"  # error | warning


@dataclass
class ImportResult:
    token: str = ""
    filename: str = ""
    detected_delimiter: str = ","
    detected_encoding: str = "utf-8"
    detected_format: str = FORMAT_SQE5  # SQE5 | LEGACY_MCQ4
    total_rows: int = 0
    valid_count: int = 0
    invalid_count: int = 0
    blank_count: int = 0
    warning_count: int = 0
    topic_count: int = 0
    question_count: int = 0
    flk1_count: int = 0
    flk2_count: int = 0
    active_count: int = 0
    inactive_count: int = 0
    subject_summary: dict = field(default_factory=dict)   # subject code -> count
    tag_summary: dict = field(default_factory=dict)        # tag code -> count
    source_summary: dict = field(default_factory=dict)     # source_type -> count
    topics_summary: dict = field(default_factory=dict)     # topic -> count (legacy)
    preview_rows: list = field(default_factory=list)
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationError] = field(default_factory=list)
    ignored_columns: list[str] = field(default_factory=list)
    has_errors: bool = False


# ---------------------------------------------------------------------------
# Subject lookup cache (built once per import call)
# ---------------------------------------------------------------------------

def _build_subject_lookup() -> dict[tuple[str, str], int]:
    """Return {(paper_upper, normalised_name) -> subject_id} for all active subjects."""
    subjects = db.session.execute(
        db.select(Subject).where(Subject.active == True)  # noqa: E712
    ).scalars().all()
    lookup: dict[tuple[str, str], int] = {}
    for s in subjects:
        key_full = (s.paper, _normalise_topic(s.full_name))
        key_short = (s.paper, _normalise_topic(s.short_name))
        key_code = (s.paper, _normalise_topic(s.code))
        lookup[key_full] = s.id
        lookup[key_short] = s.id
        lookup[key_code] = s.id
    return lookup


def _build_tag_lookup() -> dict[str, int]:
    """Return {normalised_tag_name -> tag_id} for all active tags."""
    tags = db.session.execute(
        db.select(Tag).where(Tag.active == True)  # noqa: E712
    ).scalars().all()
    lookup: dict[str, int] = {}
    for t in tags:
        lookup[_normalise_topic(t.name)] = t.id
        lookup[_normalise_topic(t.code)] = t.id
    return lookup


def _get_ml_tag_id(tag_lookup: dict[str, int]) -> Optional[int]:
    return tag_lookup.get("money_laundering") or tag_lookup.get("ml")


def _normalise_topic(s: str) -> str:
    """Lowercase, strip, collapse whitespace, replace spaces with underscores."""
    return "_".join(s.strip().lower().split())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def stage_import(file_bytes: bytes, original_filename: str, user_id: int) -> ImportResult:
    """
    Parse, validate, and stage an uploaded question file (SQE or legacy format).

    Returns ImportResult.  If has_errors is True, nothing is persisted.
    On success, result.token can be passed to confirm_import().
    """
    result = ImportResult(filename=_safe_filename(original_filename))

    # Checksum
    checksum = hashlib.sha256(file_bytes).hexdigest()

    # Decode
    text, encoding = _decode_bytes(file_bytes)
    if text is None:
        result.errors.append(
            ValidationError(row=0, field="file", message=f"File encoding not recognised. {encoding}")
        )
        result.has_errors = True
        return result

    result.detected_encoding = encoding

    # Detect delimiter
    delimiter = _detect_delimiter(text)
    result.detected_delimiter = delimiter

    # Parse headers
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

    if not raw_fieldnames:
        result.errors.append(
            ValidationError(row=0, field="file", message="File appears to be empty.")
        )
        result.has_errors = True
        return result

    # Normalise headers
    normalised_headers = [h.strip().lower() for h in raw_fieldnames]

    if len(normalised_headers) != len(set(normalised_headers)):
        result.errors.append(
            ValidationError(row=0, field="headers", message="Duplicate column headers detected.")
        )
        result.has_errors = True
        return result

    # Detect format by headers
    header_set = set(normalised_headers)
    is_sqe = _is_sqe_format(header_set)
    result.detected_format = FORMAT_SQE5 if is_sqe else FORMAT_LEGACY_MCQ4

    required = SQE_REQUIRED_HEADERS if is_sqe else LEGACY_REQUIRED_HEADERS
    missing = required - header_set
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

    # Build header map
    header_map = {norm: orig for norm, orig in zip(normalised_headers, raw_fieldnames)}
    known = required | SQE_OPTIONAL_HEADERS
    result.ignored_columns = [header_map[h] for h in normalised_headers if h not in known]

    # Build subject / tag lookup tables (database hits, so do once per import)
    subject_lookup = _build_subject_lookup() if is_sqe else {}
    tag_lookup = _build_tag_lookup() if is_sqe else {}
    ml_tag_id = _get_ml_tag_id(tag_lookup) if is_sqe else None

    # Validate rows
    valid_rows: list[dict] = []
    seen_ids: set[str] = set()
    row_num = 1  # 1-based header = row 0

    for raw_row in reader:
        row_num += 1

        # Normalise keys, preserve value whitespace for question/answers
        row: dict[str, str] = {}
        for k, v in raw_row.items():
            if k is None:
                continue
            nk = k.strip().lower()
            row[nk] = v if v else ""

        # Skip blank rows
        if not any(v.strip() for v in row.values()):
            result.blank_count += 1
            continue

        result.total_rows += 1
        row_errors: list[ValidationError] = []
        row_warnings: list[ValidationError] = []

        # ---- Question ID ----
        ext_id = row.get("question id", "").strip()
        if not ext_id:
            row_errors.append(ValidationError(row_num, "question id", "Question ID must not be blank."))
        elif ext_id in seen_ids:
            row_errors.append(ValidationError(row_num, "question id", f"Duplicate Question ID '{ext_id}'."))
        else:
            seen_ids.add(ext_id)

        # ---- Paper (SQE only) ----
        paper: Optional[str] = None
        subject_id: Optional[int] = None
        if is_sqe:
            paper_raw = row.get("paper", "").strip().upper()
            if paper_raw not in VALID_PAPERS:
                row_errors.append(ValidationError(row_num, "paper", f"Paper must be FLK1 or FLK2 (got '{paper_raw}')."))
            else:
                paper = paper_raw

        # ---- Primary Topic ----
        if is_sqe:
            primary_topic_raw = row.get("primary topic", "").strip()
            topic = primary_topic_raw
            if not topic:
                row_errors.append(ValidationError(row_num, "primary topic", "Primary Topic must not be blank."))
            elif paper:
                norm_topic = _normalise_topic(topic)
                subject_id = subject_lookup.get((paper, norm_topic))
                if subject_id is None:
                    row_errors.append(
                        ValidationError(
                            row_num,
                            "primary topic",
                            f"'{topic}' is not a recognised subject for {paper}.",
                        )
                    )
        else:
            topic = row.get("topic", "").strip()
            if not topic:
                row_errors.append(ValidationError(row_num, "topic", "Topic must not be blank."))

        # ---- Question text ----
        question_text = row.get("question", "")
        # Preserve internal whitespace; only strip edges
        question_text = question_text.strip()
        if not question_text:
            row_errors.append(ValidationError(row_num, "question", "Question text must not be blank."))
        elif len(question_text) < 10:
            row_warnings.append(ValidationError(row_num, "question", "Question text is unusually short.", "warning"))

        # ---- Answer options ----
        answer_labels = ["a", "b", "c", "d", "e"] if is_sqe else ["a", "b", "c", "d"]
        answers: dict[str, str] = {}
        for letter in answer_labels:
            val = row.get(f"answer {letter}", "").strip()
            if not val:
                row_errors.append(
                    ValidationError(row_num, f"answer {letter}", f"Answer {letter.upper()} must not be blank.")
                )
            else:
                answers[letter.upper()] = val

        # Duplicate answer check (after normalising insignificant whitespace)
        if len(answers) == len(answer_labels):
            norm_answers = [" ".join(v.lower().split()) for v in answers.values()]
            if len(set(norm_answers)) < len(norm_answers):
                row_errors.append(ValidationError(row_num, "answers", "Answer options contain exact duplicates."))
            else:
                # Warn if very similar answers
                if _answers_too_similar(answers):
                    row_warnings.append(
                        ValidationError(row_num, "answers", "Some answer options are extremely similar.", "warning")
                    )

        for v in answers.values():
            if len(v) < 2:
                row_warnings.append(
                    ValidationError(row_num, "answers", "An answer option is unusually short.", "warning")
                )
                break

        # ---- Correct answer ----
        valid_correct = VALID_ANSWERS_SQE if is_sqe else VALID_ANSWERS_LEGACY
        correct = row.get("correct answer", "").strip().upper()
        if correct not in valid_correct:
            row_errors.append(
                ValidationError(
                    row_num,
                    "correct answer",
                    f"Correct answer must be {'/'.join(sorted(valid_correct))} (got '{correct}').",
                )
            )

        # ---- Secondary tags (SQE only) ----
        tag_ids: list[int] = []
        tag_codes: list[str] = []
        if is_sqe and not row_errors:  # only parse if no hard errors yet
            tags_raw = row.get("secondary tags", "")
            if tags_raw.strip():
                for raw_tag in tags_raw.split(TAG_SEPARATOR):
                    t = raw_tag.strip()
                    if not t:
                        continue
                    tid = tag_lookup.get(_normalise_topic(t))
                    if tid is None:
                        row_warnings.append(
                            ValidationError(
                                row_num, "secondary tags", f"Unknown tag '{t}' — ignored.", "warning"
                            )
                        )
                    else:
                        if tid not in tag_ids:
                            tag_ids.append(tid)
                            tag_codes.append(t)

            # Money Laundering in FLK2 check
            if paper == "FLK2" and ml_tag_id and ml_tag_id in tag_ids:
                row_errors.append(
                    ValidationError(
                        row_num,
                        "secondary tags",
                        "Money Laundering questions belong to FLK1 only and must not appear in FLK2.",
                    )
                )

        # ---- Optional metadata ----
        explanation = row.get("explanation", "").strip() or None
        if not explanation:
            row_warnings.append(
                ValidationError(row_num, "explanation", "Missing explanation.", "warning")
            )

        authority = row.get("authority", "").strip() or None
        if not authority:
            row_warnings.append(
                ValidationError(row_num, "authority", "Missing authority / legal source.", "warning")
            )

        explanation_source = row.get("explanation source", "").strip() or None
        explanation_author = row.get("explanation author", "").strip() or None
        explanation_independent = True  # independently written unless source indicates SRA

        source_type = row.get("source type", "").strip() or None
        source_set = row.get("source set", "").strip() or None
        source_question_id = row.get("source question id", "").strip() or None
        source_version = row.get("source version", "").strip() or None
        source_url = row.get("source url", "").strip() or None
        source_notice = row.get("source notice", "").strip() or None
        subtopic = row.get("subtopic", "").strip() or None
        notes = row.get("notes", "").strip() or None
        language = row.get("language", "en").strip() or "en"

        # SRA Official Source: check attribution requirements
        if source_type and "sra" in source_type.lower() and not source_notice:
            row_warnings.append(
                ValidationError(
                    row_num,
                    "source notice",
                    "SRA-source question is missing source_notice (required disclaimer).",
                    "warning",
                )
            )
            explanation_independent = False

        # Candidate correct %
        cand_pct = None
        cand_pct_raw = row.get("candidate correct percentage", "").strip()
        if cand_pct_raw:
            try:
                pct_val = float(cand_pct_raw)
                if not 0 <= pct_val <= 100:
                    row_errors.append(
                        ValidationError(
                            row_num,
                            "candidate correct percentage",
                            f"Must be 0–100 (got '{cand_pct_raw}').",
                        )
                    )
                else:
                    cand_pct = round(pct_val, 2)
                    if not source_type:
                        row_warnings.append(
                            ValidationError(
                                row_num,
                                "candidate correct percentage",
                                "Candidate performance data provided without a recognised source.",
                                "warning",
                            )
                        )
            except ValueError:
                row_errors.append(
                    ValidationError(
                        row_num,
                        "candidate correct percentage",
                        f"Must be a number (got '{cand_pct_raw}').",
                    )
                )

        # Date fields
        def _parse_date_field(col_key: str) -> Optional[date]:
            raw = row.get(col_key, "").strip()
            if not raw:
                return None
            parsed = _parse_iso_date(raw)
            if parsed is None:
                row_errors.append(
                    ValidationError(
                        row_num, col_key, f"Date must be YYYY-MM-DD or DD/MM/YYYY (got '{raw}')."
                    )
                )
            return parsed

        law_cutoff = _parse_date_field("law cut-off date")
        valid_from = _parse_date_field("valid from")
        valid_to = _parse_date_field("valid to")
        last_reviewed = _parse_date_field("last reviewed")
        reviewed_by = row.get("reviewed by", "").strip() or None

        if not law_cutoff:
            row_warnings.append(
                ValidationError(row_num, "law cut-off date", "Missing law cut-off date.", "warning")
            )
        if not last_reviewed:
            row_warnings.append(
                ValidationError(row_num, "last reviewed", "Missing last reviewed date.", "warning")
            )

        # Review status
        review_status_raw = row.get("review status", "").strip()
        review_status = REVIEW_STATUS_DRAFT
        if review_status_raw:
            matched = next(
                (rs for rs in VALID_REVIEW_STATUSES if rs.lower() == review_status_raw.lower()),
                None,
            )
            if matched is None:
                row_errors.append(
                    ValidationError(
                        row_num,
                        "review status",
                        f"Review status must be one of: {', '.join(VALID_REVIEW_STATUSES)} (got '{review_status_raw}').",
                    )
                )
            else:
                review_status = matched
        elif source_type and "sra" in source_type.lower():
            review_status = REVIEW_STATUS_OFFICIAL

        # Active flag
        active_val = row.get("active", "").strip()
        active = True
        if active_val:
            if active_val.lower() in TRUE_VALUES:
                active = True
            elif active_val.lower() in FALSE_VALUES:
                active = False
            else:
                row_errors.append(
                    ValidationError(
                        row_num, "active", f"Active must be true/false/yes/no/1/0 (got '{active_val}')."
                    )
                )

        # Difficulty
        difficulty_raw = row.get("difficulty", "").strip()
        difficulty = None
        if difficulty_raw:
            if difficulty_raw.lower() not in VALID_DIFFICULTIES:
                row_errors.append(
                    ValidationError(
                        row_num,
                        "difficulty",
                        f"Difficulty must be Easy/Medium/Hard (got '{difficulty_raw}').",
                    )
                )
            else:
                difficulty = difficulty_raw.capitalize()

        # Image URL (legacy)
        image_url = row.get("image url", "").strip() or None
        if image_url:
            try:
                parsed = urlparse(image_url)
                if parsed.scheme not in ("http", "https"):
                    raise ValueError
            except Exception:
                row_errors.append(ValidationError(row_num, "image url", "Image URL must use http or https."))
                image_url = None

        # Collect errors
        if row_errors:
            result.errors.extend(row_errors)
            result.invalid_count += 1
        else:
            result.warnings.extend(row_warnings)

            # Build logical key for SQE questions
            logical_key = None
            if is_sqe and source_type and source_set and source_question_id:
                logical_key = f"{source_type}|{source_set}|{source_question_id}"

            # Content fingerprint (5-option for SQE, 4-option for legacy)
            if is_sqe:
                all_answers = [answers.get(l, "") for l in ["A", "B", "C", "D", "E"]]
                correct_text = answers.get(correct, "")
                fingerprint = _compute_fingerprint(
                    ext_id,
                    question_text,
                    "|".join(all_answers),
                    correct_text,
                    paper or "",
                    topic,
                )
            else:
                fingerprint = _compute_fingerprint(
                    ext_id,
                    question_text,
                    answers.get("A", ""),
                    answers.get("B", ""),
                    answers.get("C", ""),
                    answers.get("D", ""),
                    correct,
                )

            valid_rows.append(
                {
                    "external_question_id": ext_id,
                    "question_format": FORMAT_SQE5 if is_sqe else FORMAT_LEGACY_MCQ4,
                    "paper": paper,
                    "subject_id": subject_id,
                    "subtopic": subtopic,
                    "logical_key": logical_key,
                    "topic": topic,
                    "topic_key": normalize_topic_key(topic),
                    "question_text": question_text,
            # Flat columns — always populated for DB compat; for SQE5 these match options
            "answer_a": answers.get("A", ""),
            "answer_b": answers.get("B", ""),
            "answer_c": answers.get("C", ""),
            "answer_d": answers.get("D", ""),
            "answer_e": answers.get("E", "") if is_sqe else None,
            "correct_answer": correct,
                    "explanation": explanation,
                    "explanation_source": explanation_source,
                    "explanation_author": explanation_author,
                    "explanation_independent": explanation_independent,
                    "source_type": source_type,
                    "source_set": source_set,
                    "source_question_id": source_question_id,
                    "source_version": source_version,
                    "source_url": source_url,
                    "source_notice": source_notice,
                    "authority": authority,
                    "candidate_correct_pct": cand_pct,
                    "law_cutoff_date": law_cutoff,
                    "valid_from": valid_from,
                    "valid_to": valid_to,
                    "last_reviewed": last_reviewed,
                    "reviewed_by": reviewed_by,
                    "review_status": review_status,
                    "difficulty": difficulty,
                    "language": language,
                    "notes": notes,
                    "active": active,
                    "image_url": image_url,
                    "reference": row.get("reference", "").strip() or None,
                    "last_updated": row.get("last updated", "").strip() or None,
                    "content_fingerprint": fingerprint,
                    # SQE normalised option data (not stored in StagedQuestion itself)
                    "_options": [
                        {
                            "source_label": lbl,
                            "option_text": answers[lbl],
                            "is_correct": lbl == correct,
                            "source_order": i,
                        }
                        for i, lbl in enumerate(
                            ["A", "B", "C", "D", "E"] if is_sqe else ["A", "B", "C", "D"]
                        )
                        if lbl in answers
                    ] if is_sqe else [],
                    "_tag_ids": tag_ids,
                }
            )
            result.valid_count += 1

    # ---------------------------------------------------------------------------
    # File-level checks
    # ---------------------------------------------------------------------------
    if not result.errors and not valid_rows:
        result.errors.append(
            ValidationError(row=0, field="file", message="File contains no valid questions.")
        )

    if result.errors:
        result.has_errors = True
        return result

    # ---------------------------------------------------------------------------
    # Build summaries
    # ---------------------------------------------------------------------------
    topic_counts: dict[str, int] = {}
    subject_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    tag_counts: dict[str, int] = {}
    flk1_count = flk2_count = active_count = inactive_count = 0

    for vr in valid_rows:
        topic_counts[vr["topic"]] = topic_counts.get(vr["topic"], 0) + 1
        if vr["paper"] == "FLK1":
            flk1_count += 1
        elif vr["paper"] == "FLK2":
            flk2_count += 1
        if vr["active"]:
            active_count += 1
        else:
            inactive_count += 1
        if vr["source_type"]:
            source_counts[vr["source_type"]] = source_counts.get(vr["source_type"], 0) + 1
        for tid in vr.get("_tag_ids", []):
            tag_counts[str(tid)] = tag_counts.get(str(tid), 0) + 1

    result.question_count = len(valid_rows)
    result.topic_count = len(topic_counts)
    result.topics_summary = topic_counts
    result.flk1_count = flk1_count
    result.flk2_count = flk2_count
    result.active_count = active_count
    result.inactive_count = inactive_count
    result.source_summary = source_counts
    result.tag_summary = tag_counts
    result.warning_count = len(result.warnings)
    result.preview_rows = valid_rows[:10]

    # ---------------------------------------------------------------------------
    # Persist staged import
    # ---------------------------------------------------------------------------
    expiry_minutes = current_app.config.get("STAGED_IMPORT_EXPIRY_MINUTES", 60)
    token = secrets.token_hex(32)

    staged = StagedImport(
        user_id=user_id,
        token=token,
        filename=result.filename,
        checksum=checksum,
        detected_delimiter=delimiter,
        detected_encoding=result.detected_encoding,
        row_count=result.total_rows,
        question_count=result.question_count,
        topic_count=result.topic_count,
        flk1_count=flk1_count,
        flk2_count=flk2_count,
        status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=expiry_minutes),
        validation_summary={
            "total_rows": result.total_rows,
            "valid_count": result.valid_count,
            "invalid_count": result.invalid_count,
            "blank_count": result.blank_count,
            "warning_count": result.warning_count,
            "topic_count": result.topic_count,
            "flk1_count": flk1_count,
            "flk2_count": flk2_count,
            "active_count": active_count,
            "inactive_count": inactive_count,
            "question_count": result.question_count,
            "detected_format": result.detected_format,
            "source_summary": source_counts,
            "ignored_columns": result.ignored_columns,
        },
    )
    db.session.add(staged)
    db.session.flush()  # get staged.id

    for vr in valid_rows:
        # Pop SQE-only internal data before building ORM object
        options_data = vr.pop("_options", [])
        tag_ids_data = vr.pop("_tag_ids", [])

        sq = StagedQuestion(staged_import_id=staged.id, **vr)
        db.session.add(sq)
        db.session.flush()  # get sq.id for options

        for opt in options_data:
            so = StagedOption(staged_question_id=sq.id, **opt)
            db.session.add(so)

    db.session.commit()
    result.token = token
    return result


def confirm_import(token: str, user_id: int) -> QuestionBankImport:
    """
    Atomically replace the active question bank from a staged import.

    Raises ValueError if the token is invalid, expired, or not owned by user_id.
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
        detected_encoding=staged.detected_encoding,
        row_count=staged.row_count,
        question_count=staged.question_count,
        topic_count=staged.topic_count,
        flk1_count=staged.flk1_count,
        flk2_count=staged.flk2_count,
        active=False,
        status="active",
        validation_summary=staged.validation_summary,
    )
    db.session.add(bank_import)
    db.session.flush()

    # Insert new questions and their normalised options
    for sq in staged.staged_questions:
        q = Question(
            bank_import_id=bank_import.id,
            external_question_id=sq.external_question_id,
            question_format=sq.question_format,
            paper=sq.paper,
            subject_id=sq.subject_id,
            subtopic=sq.subtopic,
            logical_key=sq.logical_key,
            topic=sq.topic,
            topic_key=sq.topic_key,
            question_text=sq.question_text,
            # Flat answer columns kept for compat (SQE5 also has them populated)
            answer_a=sq.answer_a,
            answer_b=sq.answer_b,
            answer_c=sq.answer_c,
            answer_d=sq.answer_d,
            answer_e=sq.answer_e,
            correct_answer=sq.correct_answer,
            explanation=sq.explanation,
            explanation_source=sq.explanation_source,
            explanation_author=sq.explanation_author,
            explanation_independent=sq.explanation_independent,
            source_type=sq.source_type,
            source_set=sq.source_set,
            source_question_id=sq.source_question_id,
            source_version=sq.source_version,
            source_url=sq.source_url,
            source_notice=sq.source_notice,
            authority=sq.authority,
            candidate_correct_pct=sq.candidate_correct_pct,
            law_cutoff_date=sq.law_cutoff_date,
            valid_from=sq.valid_from,
            valid_to=sq.valid_to,
            last_reviewed=sq.last_reviewed,
            reviewed_by=sq.reviewed_by,
            review_status=sq.review_status,
            difficulty=sq.difficulty,
            language=sq.language,
            notes=sq.notes,
            active=sq.active,
            image_url=sq.image_url,
            reference=sq.reference,
            last_updated=sq.last_updated,
            content_fingerprint=sq.content_fingerprint,
        )
        db.session.add(q)
        db.session.flush()

        # Promote staged options to live options
        for so in sq.options:
            qo = QuestionOption(
                question_id=q.id,
                source_label=so.source_label,
                option_text=so.option_text,
                is_correct=so.is_correct,
                source_order=so.source_order,
            )
            db.session.add(qo)

    # Deactivate all previous banks
    db.session.execute(
        db.update(QuestionBankImport)
        .where(QuestionBankImport.active == True)  # noqa: E712
        .values(active=False, status="superseded")
    )

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
    """Export the active question bank as a sanitised SQE CSV string."""
    active_bank = db.session.execute(
        db.select(QuestionBankImport).where(QuestionBankImport.active == True)  # noqa: E712
    ).scalar_one_or_none()

    if not active_bank:
        return ""

    questions = db.session.execute(
        db.select(Question)
        .where(Question.bank_import_id == active_bank.id)
        .order_by(Question.paper, Question.topic_key, Question.external_question_id)
    ).scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "Question ID",
            "Paper",
            "Primary Topic",
            "Question",
            "Answer A",
            "Answer B",
            "Answer C",
            "Answer D",
            "Answer E",
            "Correct Answer",
            "Secondary Tags",
            "Explanation",
            "Source Type",
            "Source Set",
            "Source Question ID",
            "Review Status",
            "Active",
            "Difficulty",
            "Law Cut-off Date",
        ]
    )
    for q in questions:
        # For SQE5 questions, resolve answers from options table
        if q.question_format == FORMAT_SQE5 and q.options:
            opt_map = {o.source_label: o.option_text for o in sorted(q.options, key=lambda o: o.source_order)}
            correct_label = next((o.source_label for o in q.options if o.is_correct), "")
        else:
            opt_map = {
                "A": q.answer_a or "",
                "B": q.answer_b or "",
                "C": q.answer_c or "",
                "D": q.answer_d or "",
                "E": q.answer_e or "",
            }
            correct_label = q.correct_answer or ""

        writer.writerow(
            [
                _sanitise_csv_cell(q.external_question_id),
                q.paper or "",
                _sanitise_csv_cell(q.topic),
                _sanitise_csv_cell(q.question_text),
                _sanitise_csv_cell(opt_map.get("A", "")),
                _sanitise_csv_cell(opt_map.get("B", "")),
                _sanitise_csv_cell(opt_map.get("C", "")),
                _sanitise_csv_cell(opt_map.get("D", "")),
                _sanitise_csv_cell(opt_map.get("E", "")),
                correct_label,
                "",  # Secondary Tags — TODO: join from question_tags
                _sanitise_csv_cell(q.explanation or ""),
                q.source_type or "",
                q.source_set or "",
                q.source_question_id or "",
                q.review_status or "",
                "true" if q.active else "false",
                q.difficulty or "",
                q.law_cutoff_date.isoformat() if q.law_cutoff_date else "",
            ]
        )
    return output.getvalue()


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _is_sqe_format(header_set: set[str]) -> bool:
    """Detect SQE format if Paper + Primary Topic + Answer E are present."""
    return "paper" in header_set and "primary topic" in header_set and "answer e" in header_set


def _decode_bytes(file_bytes: bytes) -> tuple[Optional[str], str]:
    """Attempt to decode bytes to str. Returns (text, encoding) or (None, error)."""
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return file_bytes.decode(encoding), encoding.replace("-sig", "").upper()
        except UnicodeDecodeError:
            continue
    return None, "Could not decode file as UTF-8 or Latin-1."


def _detect_delimiter(text: str) -> str:
    """Heuristic: count tabs vs commas in the first line."""
    first_line = text.split("\n", 1)[0]
    if first_line.count("\t") >= first_line.count(","):
        return "\t"
    return ","


def _parse_iso_date(raw: str) -> Optional[date]:
    """Parse a date string in common ISO or UK formats."""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _answers_too_similar(answers: dict[str, str], threshold: float = 0.9) -> bool:
    """Simple check: if any pair of answers shares >90% character overlap."""
    import difflib
    values = list(answers.values())
    for i in range(len(values)):
        for j in range(i + 1, len(values)):
            ratio = difflib.SequenceMatcher(None, values[i].lower(), values[j].lower()).ratio()
            if ratio > threshold:
                return True
    return False


def _safe_filename(name: str) -> str:
    """Strip path components and NUL bytes from an uploaded filename."""
    import os
    basename = os.path.basename(name.replace("\\", "/"))
    safe = "".join(c for c in basename if ord(c) >= 32 and c not in '<>:"/\\|?*')
    return safe or "upload.csv"


def _sanitise_csv_cell(value: str) -> str:
    """Prefix formula-injection characters to prevent spreadsheet injection."""
    if value and value[0] in FORMULA_INJECTION_CHARS:
        return "'" + value
    return value
