#!/usr/bin/env python
"""
Database initialization script - creates all tables and adds missing columns
Designed to run as Heroku release phase before web server starts
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
    """Initialize database - create tables and add missing columns"""
    with app.app_context():
        print("=" * 80)
        print("DATABASE INITIALIZATION")
        print("=" * 80)
        
        try:
            # Get database info
            dialect = db.engine.dialect.name
            print(f"\n🔍 Database Type: {dialect}")
            
            # Create all tables from models if they don't exist
            print("\n📝 Creating tables from models...")
            db.create_all()
            print("   ✓ All tables created/verified")
            
            # Add missing columns to existing tables
            connection = db.engine.connect()
            
            try:
                if dialect == 'postgresql':
                    print("\n🔧 PostgreSQL specific setup...")
                    
                    # Check if 'read' column exists on contact_message table
                    query = """
                        SELECT column_name 
                        FROM information_schema.columns 
                        WHERE table_name = 'contact_message' 
                        AND column_name = 'read'
                    """
                    result = connection.execute(db.text(query)).fetchall()
                    
                    if not result:
                        print("   Adding 'read' column to contact_message...")
                        try:
                            connection.execute(db.text("""
                                ALTER TABLE contact_message 
                                ADD COLUMN read BOOLEAN DEFAULT FALSE
                            """))
                            connection.commit()
                            print("   ✓ 'read' column added")
                        except Exception as e:
                            print(f"   ⚠️  Could not add column: {e}")
                            connection.rollback()
                    else:
                        print("   ✓ 'read' column already exists")

                    # Backfill missing legacy user columns.
                    user_column_patches = [
                        ("name", 'ALTER TABLE "user" ADD COLUMN name VARCHAR(255)'),
                        ("role", 'ALTER TABLE "user" ADD COLUMN role VARCHAR(50) DEFAULT \'viewer\' NOT NULL'),
                        ("is_active", 'ALTER TABLE "user" ADD COLUMN is_active BOOLEAN DEFAULT TRUE NOT NULL'),
                        ("created_at", 'ALTER TABLE "user" ADD COLUMN created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW() NOT NULL'),
                        ("updated_at", 'ALTER TABLE "user" ADD COLUMN updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW() NOT NULL'),
                    ]

                    for col_name, alter_sql in user_column_patches:
                        if not _column_exists_postgres(connection, "user", col_name):
                            print(f"   Adding '{col_name}' column to user...")
                            try:
                                connection.execute(db.text(alter_sql))
                                connection.commit()
                                print(f"   ✓ '{col_name}' column added")
                            except Exception as e:
                                print(f"   ⚠️  Could not add user.{col_name}: {e}")
                                connection.rollback()
                        else:
                            print(f"   ✓ user.{col_name} already exists")
                
                elif dialect == 'sqlite':
                    print("\n🔧 SQLite specific setup...")
                    
                    # Check if 'read' column exists
                    query = "PRAGMA table_info(contact_message);"
                    result = connection.execute(db.text(query)).fetchall()
                    columns = [row[1] for row in result]
                    
                    if 'read' not in columns:
                        print("   Adding 'read' column to contact_message...")
                        try:
                            connection.execute(db.text("""
                                ALTER TABLE contact_message 
                                ADD COLUMN read BOOLEAN DEFAULT 0
                            """))
                            connection.commit()
                            print("   ✓ 'read' column added")
                        except Exception as e:
                            print(f"   ⚠️  Could not add column: {e}")
                            connection.rollback()
                    else:
                        print("   ✓ 'read' column already exists")

                    user_columns = _sqlite_table_columns(connection, "user")
                    sqlite_user_patches = [
                        ("name", 'ALTER TABLE "user" ADD COLUMN name VARCHAR(255)'),
                        ("role", 'ALTER TABLE "user" ADD COLUMN role VARCHAR(50) DEFAULT "viewer" NOT NULL'),
                        ("is_active", 'ALTER TABLE "user" ADD COLUMN is_active BOOLEAN DEFAULT 1 NOT NULL'),
                        ("created_at", 'ALTER TABLE "user" ADD COLUMN created_at DATETIME'),
                        ("updated_at", 'ALTER TABLE "user" ADD COLUMN updated_at DATETIME'),
                    ]

                    for col_name, alter_sql in sqlite_user_patches:
                        if col_name not in user_columns:
                            print(f"   Adding '{col_name}' column to user...")
                            try:
                                connection.execute(db.text(alter_sql))
                                connection.commit()
                                print(f"   ✓ '{col_name}' column added")
                            except Exception as e:
                                print(f"   ⚠️  Could not add user.{col_name}: {e}")
                                connection.rollback()
                        else:
                            print(f"   ✓ user.{col_name} already exists")
            
            finally:
                connection.close()

            # Ensure at least one admin exists for CMS access.
            User = app.User
            admin_count = User.query.filter_by(role='admin').count()
            if admin_count == 0:
                bootstrap_email = app.config.get('CONTACT_TO_EMAIL', '').strip().lower()
                if bootstrap_email:
                    bootstrap_user = User.query.filter_by(email=bootstrap_email).first()
                    if bootstrap_user:
                        bootstrap_user.role = 'admin'
                        db.session.commit()
                        print(f"   ✓ Promoted {bootstrap_email} to admin (no admins existed)")
                    else:
                        print(f"   ⚠️  No user found for CONTACT_TO_EMAIL={bootstrap_email}; admin not auto-promoted")
                else:
                    print("   ⚠️  CONTACT_TO_EMAIL not set; could not auto-promote an admin user")
            
            print("\n" + "=" * 80)
            print("✅ DATABASE INITIALIZATION COMPLETE - SAFE TO START WEB SERVER")
            print("=" * 80)
            return True
            
        except Exception as e:
            print(f"\n❌ DATABASE INITIALIZATION FAILED")
            print(f"Error: {e}")
            print("=" * 80)
            return False

if __name__ == '__main__':
    success = init_database()
    sys.exit(0 if success else 1)
