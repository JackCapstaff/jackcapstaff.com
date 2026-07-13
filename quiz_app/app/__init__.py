"""Flask application factory."""
import os
import logging
from flask import Flask

from .config import config_map
from .extensions import db, migrate, login_manager, csrf


def create_app(config_name: str = "development") -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__, instance_relative_config=True)

    # Load configuration
    cfg = config_map.get(config_name, config_map["default"])
    app.config.from_object(cfg)

    # Ensure instance path exists
    os.makedirs(app.instance_path, exist_ok=True)

    # Configure logging
    _configure_logging(app)

    # Initialise extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    # Set up Flask-Login (User is injected from main app via register_blueprints)
    @login_manager.user_loader
    def load_user(user_id: str):
        # Use the User model that was injected from the main app
        if db and hasattr(db, 'Model'):
            # Find User in the registry (it will be there after main app initializes models)
            from sqlalchemy.orm import class_mapper
            from sqlalchemy import inspect
            try:
                User = None
                for mapper in db.registry.mappers:
                    if mapper.class_.__tablename__ == 'user':
                        User = mapper.class_
                        break
                if User:
                    return db.session.get(User, int(user_id))
            except:
                pass
        return None

    login_manager.login_view = "auth.login"
    login_manager.login_message = "Please log in to access this page."
    login_manager.login_message_category = "info"

    # Import models so Alembic can discover them (User is from main app)
    from .models import question, session, subject, specification, sqe  # noqa: F401

    # Register blueprints
    from .auth import auth_bp
    from .main import main_bp
    from .admin import admin_bp
    from .testing import testing_bp
    from .results import results_bp

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(testing_bp, url_prefix="/test")
    app.register_blueprint(results_bp, url_prefix="/results")

    # Register error handlers
    from .errors import register_error_handlers
    register_error_handlers(app)

    # Register CLI commands
    from .cli import register_commands
    register_commands(app)

    return app


def _configure_logging(app: Flask) -> None:
    """Set up structured logging."""
    if not app.debug and not app.testing:
        logging.basicConfig(level=logging.INFO)
    else:
        logging.basicConfig(level=logging.DEBUG)
    # Suppress sqlalchemy engine noise in non-debug mode
    if not app.debug:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
