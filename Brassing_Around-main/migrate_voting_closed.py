"""
Migration: add voting_closed column to Event table
"""
import sqlite3
import os

db_path = 'instance/brassing_around.db'

if not os.path.exists(db_path):
    print(f"Database {db_path} not found!")
    exit(1)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

try:
    cursor.execute("PRAGMA table_info(event)")
    columns = [column[1] for column in cursor.fetchall()]

    if 'voting_closed' not in columns:
        print("Adding voting_closed column to Event table...")
        cursor.execute("ALTER TABLE event ADD COLUMN voting_closed BOOLEAN DEFAULT 0")
        conn.commit()
        print("✓ Successfully added voting_closed column!")
    else:
        print("voting_closed column already exists.")

except Exception as e:
    print(f"Error: {e}")
    conn.rollback()
finally:
    conn.close()

print("\nMigration complete!")
