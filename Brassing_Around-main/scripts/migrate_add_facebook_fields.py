"""
Run this script to add missing Facebook-related columns to the database.
Idempotent: it checks for existing columns and skips them.
Supports SQLite and PostgreSQL (basic ALTER TABLE ADD COLUMN types).

Usage:
    python scripts/migrate_add_facebook_fields.py

This expects your app to configure SQLALCHEMY_DATABASE_URI and be importable as `app`.
"""
from __future__ import annotations

import sys
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

# Import Flask app and db
try:
    # Adjust path if running from project root
    from app import app, db
except Exception as exc:
    print("Failed to import app and db from app.py:", exc)
    sys.exit(2)

engine = db.engine
inspector = inspect(engine)

# Table/column definitions to add: {table: [(col_name, sql_type_for_sqlite, sql_type_for_pg) ...]}
changes = {
    'event': [
        ('facebook_post_id', "VARCHAR(100)", "VARCHAR(100)"),
    ],
    'news_item': [
        ('facebook_post_id', "VARCHAR(100)", "VARCHAR(100)"),
    ],
    'site_settings': [
        ('auto_share_programmes', "BOOLEAN", "BOOLEAN"),
        ('auto_share_news', "BOOLEAN", "BOOLEAN"),
        ('auto_share_events', "BOOLEAN", "BOOLEAN"),
        ('auto_share_competitions', "BOOLEAN", "BOOLEAN"),
    ],
}

def has_column(table_name, column_name):
    try:
        cols = [c['name'] for c in inspector.get_columns(table_name)]
        return column_name in cols
    except Exception:
        return False

def add_column(table_name, column_name, sql_type):
    ddl = f'ALTER TABLE "{table_name}" ADD COLUMN {column_name} {sql_type}'
    # For SQLite boolean default handling, it's fine to add without default
    try:
        with engine.connect() as conn:
            conn.execute(text(ddl))
            print(f"Added column {column_name} to {table_name}")
            return True
    except SQLAlchemyError as exc:
        print(f"Failed to add column {column_name} to {table_name}: {exc}")
        return False


def main():
    dialect = engine.dialect.name.lower()
    print(f"Connected to database via dialect: {dialect}")

    for table, cols in changes.items():
        # Check if table exists
        tables = inspector.get_table_names()
        if table not in tables:
            print(f"Table '{table}' does not exist in DB; skipping")
            continue

        for col_name, sqlite_type, pg_type in cols:
            if has_column(table, col_name):
                print(f"Column {col_name} already exists on {table}; skipping")
                continue
            sql_type = sqlite_type if dialect == 'sqlite' else pg_type
            success = add_column(table, col_name, sql_type)
            if not success:
                print("Stopping due to failure. Inspect DB and re-run if appropriate.")
                return 1

    print("Migration completed.")
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
