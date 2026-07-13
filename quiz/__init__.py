"""
Quiz package — mounts the full quiz application as Flask blueprints
at /quiz on the existing jackcapstaff.com Flask app.

Usage in app.py:
    from quiz import init_quiz
    init_quiz(app, db)
"""
from .blueprints.auth import auth_bp
from .blueprints.main import main_bp
from .blueprints.admin import quiz_admin_bp
from .blueprints.testing import testing_bp
from .blueprints.results import results_bp


def init_quiz(app, db):
    """
    Initialise quiz models, store them on app, and register blueprints.
    Call after db = SQLAlchemy(app) and login_manager setup in app.py.
    """
    from .models import init_quiz_models

    models = init_quiz_models(db)
    app.QuizUser = models["QuizUser"]
    app.QuestionBankImport = models["QuestionBankImport"]
    app.Question = models["Question"]
    app.StagedImport = models["StagedImport"]
    app.StagedQuestion = models["StagedQuestion"]
    app.TestSession = models["TestSession"]
    app.TestSessionQuestion = models["TestSessionQuestion"]

    app.register_blueprint(auth_bp, url_prefix="/quiz/auth")
    app.register_blueprint(main_bp, url_prefix="/quiz")
    app.register_blueprint(quiz_admin_bp, url_prefix="/quiz/admin")
    app.register_blueprint(testing_bp, url_prefix="/quiz/test")
    app.register_blueprint(results_bp, url_prefix="/quiz/results")

    # Add Flask CLI commands
    _register_cli(app)


def _register_cli(app):
    import click

    @app.cli.command("quiz-create-admin")
    @click.option("--username", prompt=True)
    @click.option("--email", prompt=True)
    @click.option("--display-name", prompt=True)
    @click.password_option()
    def quiz_create_admin(username, email, display_name, password):
        """Create a quiz administrator account."""
        from flask import current_app
        db = current_app.extensions["sqlalchemy"]
        QuizUser = current_app.QuizUser
        existing = QuizUser.query.filter_by(username=username).first()
        if existing:
            click.echo(f"Error: username '{username}' already exists.", err=True)
            raise SystemExit(1)
        user = QuizUser(
            username=username,
            email=email,
            display_name=display_name,
            role="admin",
            active=True,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Quiz administrator '{username}' created.")

    @app.cli.command("quiz-seed")
    def quiz_seed():
        """Create demo quiz users and import sample question bank."""
        import pathlib
        from flask import current_app
        from .services.csv_import import stage_import, confirm_import

        db = current_app.extensions["sqlalchemy"]
        QuizUser = current_app.QuizUser

        for uname, email, dname, pwd, role in [
            ("quiz_admin", "quizadmin@example.com", "Quiz Admin", "Admin1234!", "admin"),
            ("alice", "alice@example.com", "Alice Smith", "Alice1234!", "user"),
            ("bob", "bob@example.com", "Bob Jones", "Bob12345!", "user"),
        ]:
            if not QuizUser.query.filter_by(username=uname).first():
                u = QuizUser(username=uname, email=email, display_name=dname, role=role, active=True)
                u.set_password(pwd)
                db.session.add(u)
        db.session.commit()
        click.echo("Created quiz_admin / Admin1234!, alice / Alice1234!, bob / Bob12345!")

        sample_path = pathlib.Path(__file__).parent.parent / "sample_data" / "quiz_sample_large.csv"
        if sample_path.exists():
            admin = QuizUser.query.filter_by(role="admin").first()
            with open(sample_path, "rb") as f:
                content = f.read()
            result = stage_import(content, "quiz_sample_large.csv", admin.id, app.config)
            if result.errors:
                click.echo(f"Import errors: {len(result.errors)}; skipping question import.")
            else:
                confirm_import(result.token, admin.id, app.config)
                click.echo(f"Imported {result.question_count} questions.")
        else:
            click.echo("sample_data/quiz_sample_large.csv not found.")

    @app.cli.command("quiz-import")
    @click.argument("filepath", type=click.Path(exists=True))
    def quiz_import(filepath):
        """Import a quiz question bank CSV."""
        import os
        from flask import current_app
        from .services.csv_import import stage_import, confirm_import

        QuizUser = current_app.QuizUser
        admin = QuizUser.query.filter_by(role="admin").first()
        if not admin:
            click.echo("No quiz admin found. Run flask quiz-create-admin first.", err=True)
            raise SystemExit(1)

        with open(filepath, "rb") as f:
            content = f.read()
        result = stage_import(content, os.path.basename(filepath), admin.id, app.config)
        if result.errors:
            for e in result.errors[:20]:
                click.echo(f"  Row {e.row}: [{e.field}] {e.message}")
            raise SystemExit(1)
        confirm_import(result.token, admin.id, app.config)
        click.echo(f"Imported {result.question_count} questions across {result.topic_count} topics.")
