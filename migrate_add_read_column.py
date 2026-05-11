#!/usr/bin/env python
"""
Database initialization script - creates all tables and adds missing columns
Designed to run as Heroku release phase before web server starts
"""

import sys
from app import app, db

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
            
            finally:
                connection.close()
            
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
