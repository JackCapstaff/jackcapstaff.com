"""Flask extension instances (no circular imports here).

NOTE: db is NOT instantiated here. It will be injected by register_quiz_blueprints()
from the main app to ensure all extensions use the same SQLAlchemy instance.
"""
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_wtf import CSRFProtect

db = None  # Will be injected by main app
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()
