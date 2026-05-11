import sys
import os

# Ensure project root is on sys.path so we can import app
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app import db, app

with app.app_context():
    db.create_all()
    tables = [t.name for t in db.metadata.sorted_tables]
    print('Created/verified tables:')
    for t in tables:
        print(' -', t)
