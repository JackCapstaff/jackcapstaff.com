"""
Migration script to add role, bio, profile_photo, and updated_at fields to users.
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
    # Add role column (default 'user')
    try:
        cursor.execute("ALTER TABLE user ADD COLUMN role VARCHAR(20) DEFAULT 'user'")
        print("✓ Added 'role' column to user table")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            print("  'role' column already exists")
        else:
            raise
    
    # Add bio column
    try:
        cursor.execute("ALTER TABLE user ADD COLUMN bio TEXT")
        print("✓ Added 'bio' column to user table")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            print("  'bio' column already exists")
        else:
            raise
    
    # Add profile_photo column
    try:
        cursor.execute("ALTER TABLE user ADD COLUMN profile_photo VARCHAR(300)")
        print("✓ Added 'profile_photo' column to user table")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            print("  'profile_photo' column already exists")
        else:
            raise
    
    # Add updated_at column (nullable, will be set when user updates profile)
    try:
        cursor.execute("ALTER TABLE user ADD COLUMN updated_at DATETIME")
        print("✓ Added 'updated_at' column to user table")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            print("  'updated_at' column already exists")
        else:
            print(f"Error adding updated_at: {e}")
    
    # Sync role field with is_admin for existing users
    cursor.execute("""
        UPDATE user 
        SET role = CASE WHEN is_admin = 1 THEN 'admin' ELSE 'user' END
        WHERE role IS NULL OR role = ''
    """)
    
    conn.commit()
    print("\n✓ Migration completed successfully!")
    print("  - Synced existing admin users to 'admin' role")
    
    # Show user table structure
    cursor.execute("PRAGMA table_info(user)")
    columns = cursor.fetchall()
    print("\nUser table structure:")
    for col in columns:
        print(f"  - {col[1]} ({col[2]})")
    
except sqlite3.Error as e:
    print(f"Error: {e}")
    conn.rollback()
finally:
    conn.close()

print("\nYou can now manage user roles from the admin panel!")
print("Role types: admin, contributor, user")
