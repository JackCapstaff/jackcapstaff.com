#!/usr/bin/env python
"""
Database migration: Add 'read' column to contact_message table
"""

import os
from app import app, db

def migrate():
    """Add missing 'read' column to contact_message table"""
    
    with app.app_context():
        # Get the database connection
        connection = db.engine.connect()
        
        try:
            # Check if we're using PostgreSQL (Heroku) or SQLite
            dialect_name = db.engine.dialect.name
            
            if dialect_name == 'postgresql':
                # PostgreSQL
                check_query = """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.columns 
                        WHERE table_name = 'contact_message' 
                        AND column_name = 'read'
                    );
                """
                result = connection.execute(db.text(check_query)).fetchone()
                column_exists = result[0] if result else False
                
                if not column_exists:
                    print("Adding 'read' column to contact_message table (PostgreSQL)...")
                    add_query = """
                        ALTER TABLE contact_message 
                        ADD COLUMN read BOOLEAN DEFAULT FALSE NOT NULL;
                    """
                    connection.execute(db.text(add_query))
                    connection.commit()
                    print("✓ Column added successfully!")
                else:
                    print("✓ Column 'read' already exists in contact_message table")
                    
            elif dialect_name == 'sqlite':
                # SQLite
                check_query = "PRAGMA table_info(contact_message);"
                result = connection.execute(db.text(check_query)).fetchall()
                column_names = [row[1] for row in result]
                
                if 'read' not in column_names:
                    print("Adding 'read' column to contact_message table (SQLite)...")
                    add_query = """
                        ALTER TABLE contact_message 
                        ADD COLUMN read BOOLEAN DEFAULT 0 NOT NULL;
                    """
                    connection.execute(db.text(add_query))
                    connection.commit()
                    print("✓ Column added successfully!")
                else:
                    print("✓ Column 'read' already exists in contact_message table")
            else:
                print(f"Unknown database dialect: {dialect_name}")
                
        except Exception as e:
            print(f"✗ Migration error: {e}")
            connection.rollback()
            raise
        finally:
            connection.close()

if __name__ == '__main__':
    migrate()
    print("\nMigration complete!")
