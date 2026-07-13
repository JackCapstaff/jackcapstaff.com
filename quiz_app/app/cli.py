"""Flask CLI commands."""
import os
import click
from flask import Flask


def register_commands(app: Flask) -> None:

    @app.cli.command("create-admin")
    @click.option("--username", prompt=True, help="Admin username")
    @click.option("--email", prompt=True, help="Admin email address")
    @click.option("--display-name", prompt=True, help="Display name")
    @click.password_option(help="Admin password")
    def create_admin(username, email, display_name, password):
        """Create the first administrator account."""
        from .extensions import db
        from .models.user import User

        existing = db.session.execute(
            db.select(User).where(User.username == username)
        ).scalar_one_or_none()
        if existing:
            click.echo(f"Error: username '{username}' already exists.", err=True)
            raise SystemExit(1)

        user = User(
            username=username,
            email=email,
            display_name=display_name,
            role="admin",
            active=True,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Administrator '{username}' created successfully.")

    @app.cli.command("seed-demo")
    def seed_demo():
        """Create demo users, question bank, and completed sessions."""
        from .extensions import db
        from .models.user import User
        from .services.csv_import import stage_import, confirm_import
        import pathlib

        click.echo("Seeding demo data…")

        # Create admin
        admin = db.session.execute(
            db.select(User).where(User.username == "admin")
        ).scalar_one_or_none()
        if not admin:
            admin = User(
                username="admin",
                email="admin@example.com",
                display_name="Administrator",
                role="admin",
                active=True,
            )
            admin.set_password("Admin1234!")
            db.session.add(admin)
            db.session.commit()
            click.echo("  Created admin / Admin1234!")

        # Create normal users
        for uname, email, dname, pwd in [
            ("alice", "alice@example.com", "Alice Smith", "Alice1234!"),
            ("bob", "bob@example.com", "Bob Jones", "Bob12345!"),
        ]:
            u = db.session.execute(
                db.select(User).where(User.username == uname)
            ).scalar_one_or_none()
            if not u:
                u = User(
                    username=uname,
                    email=email,
                    display_name=dname,
                    role="user",
                    active=True,
                )
                u.set_password(pwd)
                db.session.add(u)
        db.session.commit()
        click.echo("  Created alice / Alice1234! and bob / Bob12345!")

        # Import sample question bank
        sample_path = (
            pathlib.Path(__file__).parent.parent / "sample_data" / "sample_large.csv"
        )
        if sample_path.exists():
            with open(sample_path, "rb") as f:
                content = f.read()
            result = stage_import(content, "sample_large.csv", admin.id)
            if result.errors:
                click.echo(f"  Warning: {len(result.errors)} import errors; skipping bank load.")
            else:
                confirm_import(result.token, admin.id)
                click.echo(f"  Imported {result.question_count} questions from sample_large.csv")
        else:
            click.echo("  sample_data/sample_large.csv not found; skipping question import.")

        click.echo("Demo seed complete.")
        click.echo(
            "\nDemo credentials (development only):\n"
            "  admin / Admin1234!\n"
            "  alice / Alice1234!\n"
            "  bob   / Bob12345!"
        )

    @app.cli.command("import-questions")
    @click.argument("filepath", type=click.Path(exists=True))
    @click.option("--user-id", default=None, type=int, help="Importer user ID")
    def import_questions(filepath, user_id):
        """Import a question bank CSV, replacing the active bank."""
        from .extensions import db
        from .models.user import User
        from .services.csv_import import stage_import, confirm_import

        importer = None
        if user_id:
            importer = db.session.get(User, user_id)
            if not importer:
                click.echo(f"User {user_id} not found.", err=True)
                raise SystemExit(1)
        else:
            importer = db.session.execute(
                db.select(User).where(User.role == "admin")
            ).scalars().first()
            if not importer:
                click.echo("No admin user found. Run flask create-admin first.", err=True)
                raise SystemExit(1)

        with open(filepath, "rb") as f:
            content = f.read()

        filename = os.path.basename(filepath)
        result = stage_import(content, filename, importer.id)

        if result.errors:
            click.echo(f"Validation failed with {len(result.errors)} error(s):")
            for err in result.errors[:20]:
                click.echo(f"  Row {err.row}: [{err.field}] {err.message}")
            if len(result.errors) > 20:
                click.echo(f"  … and {len(result.errors) - 20} more.")
            raise SystemExit(1)

        confirm_import(result.token, importer.id)
        click.echo(
            f"Successfully imported {result.question_count} questions "
            f"across {result.topic_count} topics."
        )
