"""Register quiz_app blueprints with main Flask app using shared extensions.

This module provides a function to integrate quiz_app blueprints into the main
application without creating a separate Flask instance. The quiz blueprints
use the extensions (db, migrate, login_manager, csrf) from the main app.
"""
import sys
import importlib


def register_quiz_blueprints(app, db, login_manager):
    """Register quiz blueprints with the main Flask application.

    Args:
        app: Main Flask application instance
        db: SQLAlchemy instance from main app
        login_manager: Flask-Login LoginManager from main app
    """
    # Inject main app's extensions into quiz_app BEFORE any imports
    # This replaces the quiz_app's own instances with the main app's instances
    import quiz_app.app.extensions as quiz_extensions
    quiz_extensions.db = db
    quiz_extensions.login_manager = login_manager
    
    # Force reload of quiz modules to pick up injected extensions
    # This ensures any cached imports use the new db instance
    for module_name in list(sys.modules.keys()):
        if 'quiz_app' in module_name and module_name not in ('quiz_app', 'quiz_app.register_blueprints'):
            del sys.modules[module_name]

    # Now import quiz blueprints (they will use the injected db)
    from quiz_app.app.auth import auth_bp
    from quiz_app.app.main import main_bp
    from quiz_app.app.admin import admin_bp
    from quiz_app.app.testing import testing_bp
    from quiz_app.app.results import results_bp

    # Configure login manager for quiz routes
    from quiz_app.app.models.user import User

    @login_manager.user_loader
    def load_quiz_user(user_id: str):
        return db.session.get(User, int(user_id))

    # Register blueprints with /quiz prefix using unique names to avoid conflicts
    app.register_blueprint(auth_bp, url_prefix="/quiz/auth", name="quiz_auth")
    app.register_blueprint(main_bp, url_prefix="/quiz", name="quiz_main")
    app.register_blueprint(admin_bp, url_prefix="/quiz/admin", name="quiz_admin")
    app.register_blueprint(testing_bp, url_prefix="/quiz/test", name="quiz_testing")
    app.register_blueprint(results_bp, url_prefix="/quiz/results", name="quiz_results")
