#!/usr/bin/env python
"""
Database initialization script - creates all tables and adds missing columns.
Designed to run as Heroku release phase before web server starts.
"""

import sys
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
