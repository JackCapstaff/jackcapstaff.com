"""One-off migration runner for Heroku - run via: heroku run python run_migration.py"""
from app import app, db
from sqlalchemy import text

with app.app_context():
    db.session.execute(text(
        'ALTER TABLE event ADD COLUMN IF NOT EXISTS voting_closed BOOLEAN DEFAULT FALSE'
    ))
    db.session.commit()
    print("Done: voting_closed column added (or already existed).")
