"""
Database migration script to add event_photo column to Event table
Run this script to update your existing database
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
    # Check if column already exists
    cursor.execute("PRAGMA table_info(event)")
    columns = [column[1] for column in cursor.fetchall()]
    
    if 'event_photo' not in columns:
        print("Adding event_photo column to Event table...")
        cursor.execute("ALTER TABLE event ADD COLUMN event_photo VARCHAR(300)")
        conn.commit()
        print("✓ Successfully added event_photo column!")
    else:
        print("event_photo column already exists in Event table.")
    
except Exception as e:
    print(f"Error: {e}")
    conn.rollback()
finally:
    conn.close()

print("\nDatabase migration complete!")
print("You can now restart your Flask application.")
