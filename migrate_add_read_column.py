#!/usr/bin/env python
"""
Database initialization script - creates all tables and adds missing columns.
Designed to run as Heroku release phase before web server starts.
"""

import sys
from datetime import date, datetime, timezone
from app import app, db


def _column_exists_postgres(connection, table_name, column_name):
    query = db.text(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = :table_name
          AND column_name = :column_name
        LIMIT 1
        """
    )
    return connection.execute(
        query,
        {"table_name": table_name, "column_name": column_name},
    ).fetchone() is not None


def _sqlite_table_columns(connection, table_name):
    rows = connection.execute(db.text(f"PRAGMA table_info({table_name});")).fetchall()
    return {row[1] for row in rows}


def _table_exists_postgres(connection, table_name):
    result = connection.execute(
        db.text("SELECT to_regclass('public.' || :t)"),
        {"t": table_name},
    ).fetchone()
    return result is not None and result[0] is not None


def _table_exists_sqlite(connection, table_name):
    result = connection.execute(
        db.text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
        {"t": table_name},
    ).fetchone()
    return result is not None


def _col_nullable_postgres(connection, table_name, column_name):
    """Return True if the column is already nullable in PostgreSQL."""
    row = connection.execute(
        db.text(
            """
            SELECT is_nullable FROM information_schema.columns
            WHERE table_schema='public' AND table_name=:t AND column_name=:c
            LIMIT 1
            """
        ),
        {"t": table_name, "c": column_name},
    ).fetchone()
    if row is None:
        return True  # column doesn't exist — treat as nullable (will be added)
    return row[0].upper() == "YES"


def _migrate_sqe_schema(connection, dialect):
    """Add all SQE1 columns, new tables, and seed data.  Idempotent."""

    print("\nSQE1 schema upgrade...")

    # ------------------------------------------------------------------
    # Helper: add a column if it doesn't exist
    # ------------------------------------------------------------------
    def _add_col(table, col, sql_type, nullable=True, default=None, not_null_default=None):
        if dialect == "postgresql":
            if _column_exists_postgres(connection, table, col):
                return
            null_clause = "NULL" if nullable else "NOT NULL"
            default_clause = ""
            if default is not None:
                default_clause = f" DEFAULT {default}"
            elif not_null_default is not None:
                default_clause = f" DEFAULT {not_null_default}"
            try:
                connection.execute(
                    db.text(
                        f'ALTER TABLE "{table}" ADD COLUMN {col} {sql_type}'
                        f"{default_clause} {null_clause}"
                    )
                )
                connection.commit()
                print(f"   OK {table}.{col} added")
            except Exception as exc:
                print(f"   WARN {table}.{col}: {exc}")
                connection.rollback()
        elif dialect == "sqlite":
            cols = _sqlite_table_columns(connection, table)
            if col in cols:
                return
            default_clause = ""
            if default is not None:
                default_clause = f" DEFAULT {default}"
            elif not_null_default is not None:
                default_clause = f" DEFAULT {not_null_default}"
            try:
                connection.execute(
                    db.text(f"ALTER TABLE \"{table}\" ADD COLUMN {col} {sql_type}{default_clause}")
                )
                connection.commit()
                print(f"   OK {table}.{col} added")
            except Exception as exc:
                print(f"   WARN {table}.{col}: {exc}")
                connection.rollback()

    # ------------------------------------------------------------------
    # Helper: make a NOT NULL column nullable (PostgreSQL only — SQLite
    # doesn't support DROP NOT NULL; but since all new SQLite DBs are
    # created from models that already have nullable=True, this is only
    # needed for existing PostgreSQL DBs)
    # ------------------------------------------------------------------
    def _make_nullable_pg(table, col, col_type):
        if dialect != "postgresql":
            return
        if _col_nullable_postgres(connection, table, col):
            return
        try:
            connection.execute(
                db.text(f'ALTER TABLE "{table}" ALTER COLUMN {col} DROP NOT NULL')
            )
            connection.commit()
            print(f"   OK {table}.{col} made nullable")
        except Exception as exc:
            print(f"   WARN {table}.{col} DROP NOT NULL: {exc}")
            connection.rollback()

    # ------------------------------------------------------------------
    # 1. Make existing NOT NULL answer columns nullable
    # ------------------------------------------------------------------
    for tbl in ("questions", "staged_questions", "test_session_questions"):
        for col_name in ("answer_a", "answer_b", "answer_c", "answer_d", "correct_answer"):
            _make_nullable_pg(tbl, col_name, "TEXT")

    # ------------------------------------------------------------------
    # 2. New columns on question_bank_imports
    # ------------------------------------------------------------------
    _add_col("question_bank_imports", "detected_encoding", "VARCHAR(20)")
    _add_col("question_bank_imports", "flk1_count", "INTEGER", nullable=False, not_null_default="0")
    _add_col("question_bank_imports", "flk2_count", "INTEGER", nullable=False, not_null_default="0")
    _add_col("question_bank_imports", "active_count", "INTEGER", nullable=False, not_null_default="0")
    _add_col("question_bank_imports", "inactive_count", "INTEGER", nullable=False, not_null_default="0")
    _add_col("question_bank_imports", "specification_id", "INTEGER")
    _add_col("question_bank_imports", "spec_version_snapshot", "VARCHAR(120)")
    _add_col("question_bank_imports", "import_notes", "TEXT")

    # ------------------------------------------------------------------
    # 3. New columns on staged_imports
    # ------------------------------------------------------------------
    _add_col("staged_imports", "detected_encoding", "VARCHAR(20)")
    _add_col("staged_imports", "flk1_count", "INTEGER", nullable=False, not_null_default="0")
    _add_col("staged_imports", "flk2_count", "INTEGER", nullable=False, not_null_default="0")
    _add_col("staged_imports", "specification_id", "INTEGER")

    # ------------------------------------------------------------------
    # 4. New columns on questions
    # ------------------------------------------------------------------
    _add_col("questions", "question_format", "VARCHAR(16)", nullable=False, not_null_default="'LEGACY_MCQ4'")
    _add_col("questions", "paper", "VARCHAR(4)")
    _add_col("questions", "subject_id", "INTEGER")
    _add_col("questions", "subtopic", "VARCHAR(255)")
    _add_col("questions", "logical_key", "VARCHAR(300)")
    _add_col("questions", "answer_e", "TEXT")
    _add_col("questions", "explanation_source", "TEXT")
    _add_col("questions", "explanation_author", "VARCHAR(255)")
    _add_col("questions", "explanation_independent", "BOOLEAN", nullable=False, not_null_default="TRUE")
    _add_col("questions", "source_type", "VARCHAR(60)")
    _add_col("questions", "source_set", "VARCHAR(120)")
    _add_col("questions", "source_question_id", "VARCHAR(100)")
    _add_col("questions", "source_version", "VARCHAR(80)")
    _add_col("questions", "source_url", "VARCHAR(512)")
    _add_col("questions", "source_notice", "TEXT")
    _add_col("questions", "authority", "TEXT")
    _add_col("questions", "candidate_correct_pct", "NUMERIC(5,2)")
    _add_col("questions", "law_cutoff_date", "DATE")
    _add_col("questions", "valid_from", "DATE")
    _add_col("questions", "valid_to", "DATE")
    _add_col("questions", "last_reviewed", "DATE")
    _add_col("questions", "reviewed_by", "VARCHAR(255)")
    _add_col("questions", "review_status", "VARCHAR(40)", nullable=False, not_null_default="'Draft'")
    _add_col("questions", "review_notes", "TEXT")
    _add_col("questions", "language", "VARCHAR(10)", nullable=False, not_null_default="'en'")
    _add_col("questions", "notes", "TEXT")

    # ------------------------------------------------------------------
    # 5. New columns on staged_questions
    # ------------------------------------------------------------------
    _add_col("staged_questions", "question_format", "VARCHAR(16)", nullable=False, not_null_default="'LEGACY_MCQ4'")
    _add_col("staged_questions", "paper", "VARCHAR(4)")
    _add_col("staged_questions", "subject_id", "INTEGER")
    _add_col("staged_questions", "subtopic", "VARCHAR(255)")
    _add_col("staged_questions", "logical_key", "VARCHAR(300)")
    _add_col("staged_questions", "answer_e", "TEXT")
    _add_col("staged_questions", "explanation_source", "TEXT")
    _add_col("staged_questions", "explanation_author", "VARCHAR(255)")
    _add_col("staged_questions", "explanation_independent", "BOOLEAN", nullable=False, not_null_default="TRUE")
    _add_col("staged_questions", "source_type", "VARCHAR(60)")
    _add_col("staged_questions", "source_set", "VARCHAR(120)")
    _add_col("staged_questions", "source_question_id", "VARCHAR(100)")
    _add_col("staged_questions", "source_version", "VARCHAR(80)")
    _add_col("staged_questions", "source_url", "VARCHAR(512)")
    _add_col("staged_questions", "source_notice", "TEXT")
    _add_col("staged_questions", "authority", "TEXT")
    _add_col("staged_questions", "candidate_correct_pct", "NUMERIC(5,2)")
    _add_col("staged_questions", "law_cutoff_date", "DATE")
    _add_col("staged_questions", "valid_from", "DATE")
    _add_col("staged_questions", "valid_to", "DATE")
    _add_col("staged_questions", "last_reviewed", "DATE")
    _add_col("staged_questions", "reviewed_by", "VARCHAR(255)")
    _add_col("staged_questions", "review_status", "VARCHAR(40)", nullable=False, not_null_default="'Draft'")
    _add_col("staged_questions", "language", "VARCHAR(10)", nullable=False, not_null_default="'en'")
    _add_col("staged_questions", "notes", "TEXT")

    # ------------------------------------------------------------------
    # 6. New columns on test_sessions (widen mode, add SQE fields)
    # ------------------------------------------------------------------
    if dialect == "postgresql":
        try:
            connection.execute(
                db.text('ALTER TABLE test_sessions ALTER COLUMN mode TYPE VARCHAR(40)')
            )
            connection.commit()
            print("   OK test_sessions.mode widened to VARCHAR(40)")
        except Exception as exc:
            print(f"   WARN test_sessions.mode widen: {exc}")
            connection.rollback()

    _add_col("test_sessions", "paper", "VARCHAR(4)")
    _add_col("test_sessions", "blueprint_profile_id", "INTEGER")
    _add_col("test_sessions", "specification_id", "INTEGER")
    _add_col("test_sessions", "spec_version_snapshot", "VARCHAR(120)")
    _add_col("test_sessions", "blueprint_allocation_snapshot", "TEXT")
    _add_col("test_sessions", "is_strict_blueprint", "BOOLEAN", nullable=False, not_null_default="FALSE")
    _add_col("test_sessions", "section_count", "INTEGER", nullable=False, not_null_default="1")

    # ------------------------------------------------------------------
    # 7. New columns on test_session_questions
    # ------------------------------------------------------------------
    _add_col("test_session_questions", "paper", "VARCHAR(4)")
    _add_col("test_session_questions", "subject_id_snapshot", "INTEGER")
    _add_col("test_session_questions", "subject_name_snapshot", "VARCHAR(255)")
    _add_col("test_session_questions", "question_format", "VARCHAR(16)", nullable=False, not_null_default="'LEGACY_MCQ4'")
    _add_col("test_session_questions", "section_number", "INTEGER", nullable=False, not_null_default="1")
    _add_col("test_session_questions", "answer_e", "TEXT")
    _add_col("test_session_questions", "source_type_snapshot", "VARCHAR(60)")
    _add_col("test_session_questions", "source_notice_snapshot", "TEXT")

    # ------------------------------------------------------------------
    # 8. Seed canonical subjects (if table is empty)
    # ------------------------------------------------------------------
    subjects_exist = connection.execute(db.text("SELECT COUNT(*) FROM subjects")).scalar()
    if not subjects_exist:
        print("   Seeding canonical SQE1 subjects...")
        _now = datetime.now(timezone.utc).isoformat()
        subjects = [
            ("FLK1_BLP", "FLK1", "Business Law and Practice", "Business Law", 1),
            ("FLK1_DR", "FLK1", "Dispute Resolution", "Dispute Resolution", 2),
            ("FLK1_CON", "FLK1", "Contract Law", "Contract", 3),
            ("FLK1_TORT", "FLK1", "Tort", "Tort", 4),
            ("FLK1_LS", "FLK1", "The Legal System of England and Wales", "Legal System", 5),
            ("FLK1_LSVC", "FLK1", "Legal Services", "Legal Services", 6),
            ("FLK2_PLP", "FLK2", "Property Law and Practice", "Property", 1),
            ("FLK2_LAND", "FLK2", "Land Law", "Land Law", 2),
            ("FLK2_WILLS", "FLK2", "Wills and the Administration of Estates", "Wills & Estates", 3),
            ("FLK2_TRUST", "FLK2", "Trusts Law", "Trusts", 4),
            ("FLK2_CRIM", "FLK2", "Criminal Liability", "Criminal Liability", 5),
            ("FLK2_CRIMP", "FLK2", "Criminal Law and Practice", "Criminal Practice", 6),
        ]
        for code, paper, full_name, short_name, display_order in subjects:
            try:
                connection.execute(
                    db.text(
                        "INSERT INTO subjects (code, paper, full_name, short_name, display_order, active, created_at) "
                        "VALUES (:code, :paper, :full_name, :short_name, :display_order, :active, :created_at)"
                    ),
                    {
                        "code": code,
                        "paper": paper,
                        "full_name": full_name,
                        "short_name": short_name,
                        "display_order": display_order,
                        "active": True,
                        "created_at": _now,
                    },
                )
            except Exception as exc:
                print(f"   WARN seeding subject {code}: {exc}")
        connection.commit()
        print("   OK subjects seeded")

    # ------------------------------------------------------------------
    # 9. Seed canonical tags (if table is empty)
    # ------------------------------------------------------------------
    tags_exist = connection.execute(db.text("SELECT COUNT(*) FROM tags")).scalar()
    if not tags_exist:
        print("   Seeding canonical SQE1 tags...")
        _now = datetime.now(timezone.utc).isoformat()
        tags = [
            ("ETHICS", "Ethics", "cross_cutting", True, False),
            ("PROF_CONDUCT", "Professional Conduct", "cross_cutting", True, False),
            ("MONEY_LAUNDERING", "Money Laundering", "cross_cutting", True, True),
            ("SRA_ACCOUNTS", "Solicitors Accounts", "professional", False, False),
            ("CLIENT_CARE", "Client Care", "professional", False, False),
            ("CONFLICTS", "Conflicts of Interest", "professional", False, False),
            ("CONFIDENTIALITY", "Confidentiality", "professional", False, False),
            ("UNDERTAKINGS", "Undertakings", "professional", False, False),
            ("SRA_ACCOUNTS_RULES", "SRA Accounts Rules", "professional", False, False),
            ("TAXATION", "Taxation", "flk1", False, False),
            ("BIZ_ORGS", "Business Organisations", "flk1", False, False),
            ("CIVIL_PROC", "Civil Procedure", "flk1", False, False),
            ("EVIDENCE", "Evidence", "shared", False, False),
            ("REMEDIES", "Remedies", "shared", False, False),
            ("COSTS", "Costs", "shared", False, False),
            ("CONVEYANCING", "Conveyancing", "flk2", False, False),
            ("LEASEHOLD", "Leasehold", "flk2", False, False),
            ("FREEHOLD", "Freehold", "flk2", False, False),
            ("PROBATE", "Probate", "flk2", False, False),
            ("INTESTACY", "Intestacy", "flk2", False, False),
            ("ESTATE_ADMIN", "Estate Administration", "flk2", False, False),
            ("TRUSTEE_DUTIES", "Trustee Duties", "flk2", False, False),
            ("POLICE_POWERS", "Police Powers", "criminal", False, False),
            ("CRIM_EVIDENCE", "Criminal Evidence", "criminal", False, False),
            ("SENTENCING", "Sentencing", "criminal", False, False),
        ]
        for code, name, category, is_cross_cutting, is_flk1_only in tags:
            try:
                connection.execute(
                    db.text(
                        "INSERT INTO tags (code, name, category, is_cross_cutting, is_flk1_only, active, created_at) "
                        "VALUES (:code, :name, :category, :is_cross_cutting, :is_flk1_only, :active, :created_at)"
                    ),
                    {
                        "code": code,
                        "name": name,
                        "category": category,
                        "is_cross_cutting": is_cross_cutting,
                        "is_flk1_only": is_flk1_only,
                        "active": True,
                        "created_at": _now,
                    },
                )
            except Exception as exc:
                print(f"   WARN seeding tag {code}: {exc}")
        connection.commit()
        print("   OK tags seeded")

    # ------------------------------------------------------------------
    # 10. Seed default AssessmentSpecification + BlueprintProfiles
    # ------------------------------------------------------------------
    spec_exists = connection.execute(
        db.text("SELECT COUNT(*) FROM assessment_specifications")
    ).scalar()
    if not spec_exists:
        print("   Seeding default assessment specification and blueprint profiles...")
        _now = datetime.now(timezone.utc).isoformat()
        try:
            if dialect == "postgresql":
                spec_id_row = connection.execute(
                    db.text(
                        """
                        INSERT INTO assessment_specifications
                            (name, effective_from, active, source_url, description, created_at)
                        VALUES
                            ('SQE1 Specification (2025-2026)', '2025-01-01', TRUE,
                             'https://sqe.sra.org.uk/assessments/sqe1-assessments/sqe1-specification',
                             'Default SQE1 specification seeded at install. Update when SRA publishes a new version.',
                             :now)
                        RETURNING id
                        """
                    ),
                    {"now": _now},
                ).fetchone()
                spec_id = spec_id_row[0] if spec_id_row else None
            else:
                connection.execute(
                    db.text(
                        """
                        INSERT INTO assessment_specifications
                            (name, effective_from, active, source_url, description, created_at)
                        VALUES
                            ('SQE1 Specification (2025-2026)', '2025-01-01', 1,
                             'https://sqe.sra.org.uk/assessments/sqe1-assessments/sqe1-specification',
                             'Default SQE1 specification seeded at install. Update when SRA publishes a new version.',
                             :now)
                        """
                    ),
                    {"now": _now},
                )
                spec_id = connection.execute(
                    db.text("SELECT id FROM assessment_specifications LIMIT 1")
                ).scalar()
            connection.commit()

            if spec_id:
                for paper, bp_name in [("FLK1", "FLK1 Blueprint 2025-2026"), ("FLK2", "FLK2 Blueprint 2025-2026")]:
                    if dialect == "postgresql":
                        bp_id_row = connection.execute(
                            db.text(
                                """
                                INSERT INTO blueprint_profiles
                                    (specification_id, paper, name, strict_min_questions,
                                     cross_cutting_cap, allow_option_randomise, active, created_at)
                                VALUES (:sid, :paper, :name, 30, 0.2, FALSE, TRUE, :now)
                                RETURNING id
                                """
                            ),
                            {"sid": spec_id, "paper": paper, "name": bp_name, "now": _now},
                        ).fetchone()
                        bp_id = bp_id_row[0] if bp_id_row else None
                    else:
                        connection.execute(
                            db.text(
                                """
                                INSERT INTO blueprint_profiles
                                    (specification_id, paper, name, strict_min_questions,
                                     cross_cutting_cap, allow_option_randomise, active, created_at)
                                VALUES (:sid, :paper, :name, 30, 0.2, 0, 1, :now)
                                """
                            ),
                            {"sid": spec_id, "paper": paper, "name": bp_name, "now": _now},
                        )
                        bp_id = connection.execute(
                            db.text(
                                "SELECT id FROM blueprint_profiles WHERE specification_id=:sid AND paper=:paper LIMIT 1"
                            ),
                            {"sid": spec_id, "paper": paper},
                        ).scalar()
                    connection.commit()

                    if bp_id:
                        if paper == "FLK1":
                            subj_allocs = [
                                ("FLK1_BLP", "0.14", "0.20", "0.17"),
                                ("FLK1_DR",  "0.14", "0.20", "0.17"),
                                ("FLK1_CON", "0.14", "0.20", "0.17"),
                                ("FLK1_TORT","0.14", "0.20", "0.17"),
                                ("FLK1_LS",  "0.14", "0.20", "0.17"),
                                ("FLK1_LSVC","0.12", "0.16", "0.15"),
                            ]
                        else:
                            subj_allocs = [
                                ("FLK2_PLP",  "0.14", "0.20", "0.1667"),
                                ("FLK2_LAND", "0.14", "0.20", "0.1667"),
                                ("FLK2_WILLS","0.14", "0.20", "0.1667"),
                                ("FLK2_TRUST","0.14", "0.20", "0.1667"),
                                ("FLK2_CRIM", "0.14", "0.20", "0.1667"),
                                ("FLK2_CRIMP","0.14", "0.20", "0.1667"),
                            ]
                        for code, min_p, max_p, tgt_p in subj_allocs:
                            subj_id = connection.execute(
                                db.text("SELECT id FROM subjects WHERE code=:code"), {"code": code}
                            ).scalar()
                            if subj_id:
                                connection.execute(
                                    db.text(
                                        "INSERT INTO blueprint_subjects (profile_id, subject_id, min_pct, max_pct, target_pct) "
                                        "VALUES (:pid, :sid, :min_p, :max_p, :tgt_p)"
                                    ),
                                    {"pid": bp_id, "sid": subj_id, "min_p": min_p, "max_p": max_p, "tgt_p": tgt_p},
                                )
                        connection.commit()
                        print(f"   OK blueprint profile {bp_name} seeded")
        except Exception as exc:
            print(f"   WARN seeding spec/blueprint: {exc}")
            try:
                connection.rollback()
            except Exception:
                pass

    print("   SQE1 schema upgrade complete")


def init_database():
    """Initialize database - create tables and add missing columns."""
    with app.app_context():
        print("=" * 80)
        print("DATABASE INITIALIZATION")
        print("=" * 80)

        try:
            dialect = db.engine.dialect.name
            print(f"\nDatabase Type: {dialect}")

            print("\nCreating tables from models...")
            db.create_all()
            print("   OK all tables created/verified")

            connection = db.engine.connect()
            try:
                if dialect == "postgresql":
                    print("\nPostgreSQL specific setup...")

                    # contact_message.read
                    if not _column_exists_postgres(connection, "contact_message", "read"):
                        print("   Adding contact_message.read...")
                        try:
                            connection.execute(db.text("ALTER TABLE contact_message ADD COLUMN read BOOLEAN DEFAULT FALSE"))
                            connection.commit()
                            print("   OK contact_message.read added")
                        except Exception as e:
                            print(f"   WARN could not add contact_message.read: {e}")
                            connection.rollback()
                    else:
                        print("   OK contact_message.read already exists")

                    # product.youtube_url
                    if not _column_exists_postgres(connection, "product", "youtube_url"):
                        print("   Adding product.youtube_url...")
                        try:
                            connection.execute(db.text("ALTER TABLE product ADD COLUMN youtube_url VARCHAR(512)"))
                            connection.commit()
                            print("   OK product.youtube_url added")
                        except Exception as e:
                            print(f"   WARN could not add product.youtube_url: {e}")
                            connection.rollback()
                    else:
                        print("   OK product.youtube_url already exists")

                    # site_setting table
                    site_setting_exists = connection.execute(db.text("SELECT to_regclass('public.site_setting')")).fetchone()
                    if not site_setting_exists or not site_setting_exists[0]:
                        print("   Creating site_setting table...")
                        try:
                            connection.execute(db.text(
                                """
                                CREATE TABLE site_setting (
                                    id SERIAL PRIMARY KEY,
                                    key VARCHAR(64) UNIQUE NOT NULL,
                                    value VARCHAR(255) NOT NULL,
                                    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW() NOT NULL
                                )
                                """
                            ))
                            connection.commit()
                            print("   OK site_setting table created")
                        except Exception as e:
                            print(f"   WARN could not create site_setting: {e}")
                            connection.rollback()
                    else:
                        print("   OK site_setting table already exists")

                    user_column_patches = [
                        ("name", 'ALTER TABLE "user" ADD COLUMN name VARCHAR(255)'),
                        ("role", 'ALTER TABLE "user" ADD COLUMN role VARCHAR(50) DEFAULT \'viewer\' NOT NULL'),
                        ("is_active", 'ALTER TABLE "user" ADD COLUMN is_active BOOLEAN DEFAULT TRUE NOT NULL'),
                        ("created_at", 'ALTER TABLE "user" ADD COLUMN created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW() NOT NULL'),
                        ("updated_at", 'ALTER TABLE "user" ADD COLUMN updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW() NOT NULL'),
                        ("reset_token", 'ALTER TABLE "user" ADD COLUMN reset_token VARCHAR(255) UNIQUE'),
                        ("reset_token_expiry", 'ALTER TABLE "user" ADD COLUMN reset_token_expiry TIMESTAMP WITHOUT TIME ZONE'),
                    ]
                    for col_name, alter_sql in user_column_patches:
                        if not _column_exists_postgres(connection, "user", col_name):
                            print(f"   Adding user.{col_name}...")
                            try:
                                connection.execute(db.text(alter_sql))
                                connection.commit()
                                print(f"   OK user.{col_name} added")
                            except Exception as e:
                                print(f"   WARN could not add user.{col_name}: {e}")
                                connection.rollback()
                        else:
                            print(f"   OK user.{col_name} already exists")

                    shop_item_patches = [
                        ("download_access_limit", "ALTER TABLE shop_order_item ADD COLUMN download_access_limit INTEGER DEFAULT 3 NOT NULL"),
                        ("download_access_count", "ALTER TABLE shop_order_item ADD COLUMN download_access_count INTEGER DEFAULT 0 NOT NULL"),
                        ("first_downloaded_at", "ALTER TABLE shop_order_item ADD COLUMN first_downloaded_at TIMESTAMP WITHOUT TIME ZONE"),
                        ("last_downloaded_at", "ALTER TABLE shop_order_item ADD COLUMN last_downloaded_at TIMESTAMP WITHOUT TIME ZONE"),
                    ]
                    for col_name, alter_sql in shop_item_patches:
                        if not _column_exists_postgres(connection, "shop_order_item", col_name):
                            print(f"   Adding shop_order_item.{col_name}...")
                            try:
                                connection.execute(db.text(alter_sql))
                                connection.commit()
                                print(f"   OK shop_order_item.{col_name} added")
                            except Exception as e:
                                print(f"   WARN could not add shop_order_item.{col_name}: {e}")
                                connection.rollback()
                        else:
                            print(f"   OK shop_order_item.{col_name} already exists")

                    if not _column_exists_postgres(connection, "publishing_quote", "title"):
                        print("   Adding publishing_quote.title...")
                        try:
                            connection.execute(db.text("ALTER TABLE publishing_quote ADD COLUMN title VARCHAR(255)"))
                            connection.commit()
                            print("   OK publishing_quote.title added")
                        except Exception as e:
                            print(f"   WARN could not add publishing_quote.title: {e}")
                            connection.rollback()
                    else:
                        print("   OK publishing_quote.title already exists")

                elif dialect == "sqlite":
                    print("\nSQLite specific setup...")

                    contact_cols = _sqlite_table_columns(connection, "contact_message")
                    if "read" not in contact_cols:
                        print("   Adding contact_message.read...")
                        try:
                            connection.execute(db.text("ALTER TABLE contact_message ADD COLUMN read BOOLEAN DEFAULT 0"))
                            connection.commit()
                            print("   OK contact_message.read added")
                        except Exception as e:
                            print(f"   WARN could not add contact_message.read: {e}")
                            connection.rollback()
                    else:
                        print("   OK contact_message.read already exists")

                    product_cols = _sqlite_table_columns(connection, "product")
                    if "youtube_url" not in product_cols:
                        print("   Adding product.youtube_url...")
                        try:
                            connection.execute(db.text("ALTER TABLE product ADD COLUMN youtube_url VARCHAR(512)"))
                            connection.commit()
                            print("   OK product.youtube_url added")
                        except Exception as e:
                            print(f"   WARN could not add product.youtube_url: {e}")
                            connection.rollback()
                    else:
                        print("   OK product.youtube_url already exists")

                    tables = set(row[0] for row in connection.execute(db.text("SELECT name FROM sqlite_master WHERE type='table';")).fetchall())
                    if "site_setting" not in tables:
                        print("   Creating site_setting table...")
                        try:
                            connection.execute(db.text(
                                """
                                CREATE TABLE site_setting (
                                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                                    key VARCHAR(64) UNIQUE NOT NULL,
                                    value VARCHAR(255) NOT NULL,
                                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
                                )
                                """
                            ))
                            connection.commit()
                            print("   OK site_setting table created")
                        except Exception as e:
                            print(f"   WARN could not create site_setting: {e}")
                            connection.rollback()
                    else:
                        print("   OK site_setting table already exists")

                    user_columns = _sqlite_table_columns(connection, "user")
                    sqlite_user_patches = [
                        ("name", 'ALTER TABLE "user" ADD COLUMN name VARCHAR(255)'),
                        ("role", 'ALTER TABLE "user" ADD COLUMN role VARCHAR(50) DEFAULT "viewer" NOT NULL'),
                        ("is_active", 'ALTER TABLE "user" ADD COLUMN is_active BOOLEAN DEFAULT 1 NOT NULL'),
                        ("reset_token", 'ALTER TABLE "user" ADD COLUMN reset_token VARCHAR(255)'),
                        ("reset_token_expiry", 'ALTER TABLE "user" ADD COLUMN reset_token_expiry DATETIME'),
                        ("created_at", 'ALTER TABLE "user" ADD COLUMN created_at DATETIME'),
                        ("updated_at", 'ALTER TABLE "user" ADD COLUMN updated_at DATETIME'),
                    ]
                    for col_name, alter_sql in sqlite_user_patches:
                        if col_name not in user_columns:
                            print(f"   Adding user.{col_name}...")
                            try:
                                connection.execute(db.text(alter_sql))
                                connection.commit()
                                print(f"   OK user.{col_name} added")
                            except Exception as e:
                                print(f"   WARN could not add user.{col_name}: {e}")
                                connection.rollback()
                        else:
                            print(f"   OK user.{col_name} already exists")

                    shop_item_columns = _sqlite_table_columns(connection, "shop_order_item")
                    sqlite_shop_item_patches = [
                        ("download_access_limit", "ALTER TABLE shop_order_item ADD COLUMN download_access_limit INTEGER DEFAULT 3 NOT NULL"),
                        ("download_access_count", "ALTER TABLE shop_order_item ADD COLUMN download_access_count INTEGER DEFAULT 0 NOT NULL"),
                        ("first_downloaded_at", "ALTER TABLE shop_order_item ADD COLUMN first_downloaded_at DATETIME"),
                        ("last_downloaded_at", "ALTER TABLE shop_order_item ADD COLUMN last_downloaded_at DATETIME"),
                    ]
                    for col_name, alter_sql in sqlite_shop_item_patches:
                        if col_name not in shop_item_columns:
                            print(f"   Adding shop_order_item.{col_name}...")
                            try:
                                connection.execute(db.text(alter_sql))
                                connection.commit()
                                print(f"   OK shop_order_item.{col_name} added")
                            except Exception as e:
                                print(f"   WARN could not add shop_order_item.{col_name}: {e}")
                                connection.rollback()
                        else:
                            print(f"   OK shop_order_item.{col_name} already exists")

                    publishing_quote_columns = _sqlite_table_columns(connection, "publishing_quote")
                    if "title" not in publishing_quote_columns:
                        print("   Adding publishing_quote.title...")
                        try:
                            connection.execute(db.text("ALTER TABLE publishing_quote ADD COLUMN title VARCHAR(255)"))
                            connection.commit()
                            print("   OK publishing_quote.title added")
                        except Exception as e:
                            print(f"   WARN could not add publishing_quote.title: {e}")
                            connection.rollback()
                    else:
                        print("   OK publishing_quote.title already exists")

                # SQE1 schema upgrade — runs for both PostgreSQL and SQLite
                _migrate_sqe_schema(connection, dialect)

            finally:
                connection.close()

            # Ensure at least one admin exists for CMS access.
            User = app.User
            admin_count = User.query.filter_by(role="admin").count()
            if admin_count == 0:
                bootstrap_email = app.config.get("CONTACT_TO_EMAIL", "").strip().lower()
                if bootstrap_email:
                    bootstrap_user = User.query.filter_by(email=bootstrap_email).first()
                    if bootstrap_user:
                        bootstrap_user.role = "admin"
                        db.session.commit()
                        print(f"   OK promoted {bootstrap_email} to admin (no admins existed)")
                    else:
                        print(f"   WARN no user found for CONTACT_TO_EMAIL={bootstrap_email}; admin not auto-promoted")
                else:
                    print("   WARN CONTACT_TO_EMAIL not set; could not auto-promote an admin user")

            print("\n" + "=" * 80)
            print("DATABASE INITIALIZATION COMPLETE - SAFE TO START WEB SERVER")
            print("=" * 80)
            return True

        except Exception as e:
            print("\nDATABASE INITIALIZATION FAILED")
            print(f"Error: {e}")
            print("=" * 80)
            return False


if __name__ == "__main__":
    success = init_database()
    sys.exit(0 if success else 1)
