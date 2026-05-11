"""
Create Initial Admin User

Run this script to create the first admin user for your Brassing Around site.
This should only be used once during initial setup.

Usage:
    python create_admin.py
"""

from app import app, db, User
from getpass import getpass

def create_admin():
    with app.app_context():
        # Check if database exists
        db.create_all()
        
        print("=" * 50)
        print("Create Initial Admin User")
        print("=" * 50)
        
        # Check if any admins already exist
        existing_admin = User.query.filter_by(is_admin=True).first()
        if existing_admin:
            print(f"\n⚠️  An admin user already exists: {existing_admin.username}")
            response = input("Do you want to create another admin? (yes/no): ")
            if response.lower() not in ['yes', 'y']:
                print("Cancelled.")
                return
        
        print("\nEnter details for the new admin user:")
        username = input("Username: ").strip()
        email = input("Email: ").strip()
        password = getpass("Password: ")
        password_confirm = getpass("Confirm Password: ")
        
        if password != password_confirm:
            print("\n❌ Passwords don't match!")
            return
        
        if not username or not email or not password:
            print("\n❌ All fields are required!")
            return
        
        # Check if username exists
        if User.query.filter_by(username=username).first():
            print(f"\n❌ Username '{username}' already exists!")
            return
        
        # Check if email exists
        if User.query.filter_by(email=email).first():
            print(f"\n❌ Email '{email}' already exists!")
            return
        
        # Create admin user
        admin = User(username=username, email=email, is_admin=True)
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()
        
        print(f"\n✅ Admin user '{username}' created successfully!")
        print("\nYou can now:")
        print("1. Login at http://localhost:5000/login")
        print("2. Access the Admin Dashboard")
        print("3. Create invitation codes for other admins")
        print("=" * 50)

if __name__ == '__main__':
    create_admin()
