"""
Migration script to add the Profile table to the database.
Run this script once to update your database schema.
"""

import sqlite3
import os

# Database path
db_path = os.path.join('instance', 'brassing_around.db')

if not os.path.exists(db_path):
    print(f"Database not found at: {db_path}")
    print("Please run the Flask app first to create the database.")
    exit(1)

# Connect to database
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

try:
    # Create the profile table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS profile (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(200) NOT NULL,
            bio TEXT NOT NULL,
            photo VARCHAR(300),
            display_order INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    print("✓ Profile table created successfully!")
    
    # Check if table exists and show structure
    cursor.execute("PRAGMA table_info(profile)")
    columns = cursor.fetchall()
    print("\nProfile table structure:")
    for col in columns:
        print(f"  - {col[1]} ({col[2]})")
    
except sqlite3.Error as e:
    print(f"Error: {e}")
    conn.rollback()
finally:
    conn.close()

print("\nMigration complete! You can now create profiles in the admin panel.")
