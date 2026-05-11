#!/usr/bin/env python
"""
Initialize the database with a test admin user for development.
Usage: python init_db.py
"""

import os
import sys
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

def create_app():
    """Create Flask app for database initialization"""
    app = Flask(__name__)
    
    # Database config
    database_url = os.environ.get('DATABASE_URL', '').strip()
    if database_url:
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    else:
        base_dir = os.path.abspath(os.path.dirname(__file__))
        database_url = 'sqlite:///' + os.path.join(base_dir, 'jackcapstaff.db')
    
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    return app

app = create_app()
db = SQLAlchemy(app)

# Import and initialize models
from models import init_models
models = init_models(db)
User = models['User']

def init_db():
    """Initialize the database with tables and a test admin user."""
    with app.app_context():
        # Drop existing tables first
        print("Dropping existing tables...")
        db.drop_all()
        print("✓ Tables dropped")
        
        # Create all tables fresh
        print("Creating database tables...")
        db.create_all()
        print("✓ Tables created")

        # Check if admin user already exists
        admin = User.query.filter_by(username='admin').first()
        if admin:
            print("✓ Admin user already exists (admin@example.com)")
            return

        # Create test admin user
        print("\nCreating test admin user...")
        admin = User(
            username='admin',
            email='admin@example.com',
            name='Admin User',
            role='admin',
            is_active=True
        )
        admin.set_password('admin123')  # Change this in production!
        
        db.session.add(admin)
        db.session.commit()
        
        print("✓ Admin user created")
        print("\nLogin credentials:")
        print("  Username: admin")
        print("  Email: admin@example.com")
        print("  Password: admin123")
        print("\n⚠️  Please change the password after first login!")
        print("   Access the admin panel at: /admin/users")

if __name__ == '__main__':
    try:
        init_db()
        print("\n✓ Database initialization complete!")
    except Exception as e:
        print(f"✗ Error: {e}", file=sys.stderr)
        sys.exit(1)
