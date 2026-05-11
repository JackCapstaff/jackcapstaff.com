"""
Migration script to add Facebook settings and invitation roles.
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
    print("=== Starting Migration ===\n")
    
    # Add role column to admin_invitation table
    try:
        cursor.execute("ALTER TABLE admin_invitation ADD COLUMN role VARCHAR(20) DEFAULT 'admin'")
        print("✓ Added 'role' column to admin_invitation table")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            print("  'role' column already exists in admin_invitation table")
        else:
            raise
    
    # Add Facebook fields to site_settings table
    try:
        cursor.execute("ALTER TABLE site_settings ADD COLUMN facebook_page_id VARCHAR(200)")
        print("✓ Added 'facebook_page_id' column to site_settings table")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            print("  'facebook_page_id' column already exists")
        else:
            print(f"  Note: {e}")
    
    try:
        cursor.execute("ALTER TABLE site_settings ADD COLUMN facebook_access_token VARCHAR(500)")
        print("✓ Added 'facebook_access_token' column to site_settings table")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            print("  'facebook_access_token' column already exists")
        else:
            print(f"  Note: {e}")
    
    try:
        cursor.execute("ALTER TABLE site_settings ADD COLUMN auto_share_stories BOOLEAN DEFAULT 0")
        print("✓ Added 'auto_share_stories' column to site_settings table")
    except sqlite3.OperationalError as e:
        if "duplicate column" in str(e).lower():
            print("  'auto_share_stories' column already exists")
        else:
            print(f"  Note: {e}")
    
    # Update existing invitations to have 'admin' role by default
    cursor.execute("UPDATE admin_invitation SET role = 'admin' WHERE role IS NULL OR role = ''")
    
    conn.commit()
    print("\n✓ Migration completed successfully!")
    
    # Show admin_invitation table structure
    cursor.execute("PRAGMA table_info(admin_invitation)")
    columns = cursor.fetchall()
    print("\nAdmin Invitation table structure:")
    for col in columns:
        print(f"  - {col[1]} ({col[2]})")
    
    # Show site_settings table structure
    cursor.execute("PRAGMA table_info(site_settings)")
    columns = cursor.fetchall()
    print("\nSite Settings table structure:")
    for col in columns:
        print(f"  - {col[1]} ({col[2]})")
    
    print("\n" + "="*50)
    print("You can now:")
    print("  1. Create invitations with specific roles (admin/contributor)")
    print("  2. Configure Facebook integration in Site Settings")
    print("  3. Enable auto-sharing of stories to Facebook")
    print("="*50)
    
except sqlite3.Error as e:
    conn.rollback()
    print(f"\n✗ Migration failed: {e}")
    exit(1)
finally:
    conn.close()
