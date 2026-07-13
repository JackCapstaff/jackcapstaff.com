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
    # Inject main app's extensions into quiz_app IMMEDIATELY, before any imports
    # This is critical: models reference quiz_app.app.extensions.db, so we must
    # set it before importing anything that might load models
    import quiz_app.app.extensions as quiz_extensions
    quiz_extensions.db = db
    quiz_extensions.login_manager = login_manager
    quiz_extensions.User = app.User  # Also inject the main app's User model
    
    # NOW import quiz models (they will use the injected db)
    # This must happen BEFORE importing blueprints so models are configured correctly
    from quiz_app.app.models import question as quiz_question  # noqa: F401
    from quiz_app.app.models import session as quiz_session  # noqa: F401

    # Now import quiz blueprints (they will use the injected db and models)
    from quiz_app.app.auth import auth_bp
    from quiz_app.app.main import main_bp
    from quiz_app.app.admin import admin_bp
    from quiz_app.app.testing import testing_bp
    from quiz_app.app.results import results_bp

    # Configure login manager for quiz routes
    # Use main app's User model
    User = app.User

    @login_manager.user_loader
    def load_user(user_id: str):
        return db.session.get(User, int(user_id))

    # Register blueprints with /quiz prefix using unique names to avoid conflicts
    app.register_blueprint(auth_bp, url_prefix="/quiz/auth", name="quiz_auth")
    app.register_blueprint(main_bp, url_prefix="/quiz", name="quiz_main")
    app.register_blueprint(admin_bp, url_prefix="/quiz/admin", name="quiz_admin")
    app.register_blueprint(testing_bp, url_prefix="/quiz/test", name="quiz_testing")
    app.register_blueprint(results_bp, url_prefix="/quiz/results", name="quiz_results")





