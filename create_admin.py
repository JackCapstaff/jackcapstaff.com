#!/usr/bin/env python
"""Create a test admin user."""

from app import app, db

with app.app_context():
    User = app.User
    
    # Check if admin user exists
    admin = User.query.filter_by(username='admin').first()
    if admin:
        print("Admin user already exists")
    else:
        admin = User(
            username='admin',
            email='admin@example.com',
            name='Admin User',
            role='admin',
            is_active=True
        )
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()
        print("Admin user created: username=admin, password=admin123")
    
    # List all users
    users = User.query.all()
    print(f"\nTotal users in database: {len(users)}")
    for u in users:
        print(f"  - {u.username} ({u.email}) - role: {u.role}")
