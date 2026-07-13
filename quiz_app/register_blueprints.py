"""Register quiz_app blueprints with main Flask app using shared extensions.

This module provides a function to integrate quiz_app blueprints into the main
application without creating a separate Flask instance. The quiz blueprints
use the extensions (db, migrate, login_manager, csrf) from the main app.
"""


def register_quiz_blueprints(app, db, login_manager):
    """Register quiz blueprints with the main Flask application.

    Args:
        app: Main Flask application instance
        db: SQLAlchemy instance from main app
        login_manager: Flask-Login LoginManager from main app
    """
    # Import quiz blueprints
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
