"""Quiz auth blueprint: login, logout, register, change password."""
from flask import (
    Blueprint, current_app, flash, redirect, render_template, request, session, url_for
)
from ..auth_utils import QUIZ_USER_KEY, get_current_quiz_user, quiz_login_required

auth_bp = Blueprint("quiz_auth", __name__, template_folder="../../templates/quiz/auth")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get(QUIZ_USER_KEY):
        return redirect(url_for("quiz_main.index"))

    if request.method == "POST":
        _check_csrf()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        QuizUser = current_app.QuizUser
        user = QuizUser.query.filter_by(username=username).first()
        if user and user.active and user.check_password(password):
            session[QUIZ_USER_KEY] = user.id
            session.permanent = True
            flash("Logged in successfully.", "success")
            next_url = request.form.get("next") or url_for("quiz_main.index")
            return redirect(_safe_redirect(next_url))
        flash("Invalid username or password.", "danger")

    return render_template("quiz/auth/login.html", next=request.args.get("next", ""))


@auth_bp.route("/logout")
def logout():
    session.pop(QUIZ_USER_KEY, None)
    flash("You have been logged out.", "info")
    return redirect(url_for("quiz_auth.login"))


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if not current_app.config.get("QUIZ_REGISTRATION_ENABLED", True):
        flash("Registration is currently disabled.", "warning")
        return redirect(url_for("quiz_auth.login"))

    if request.method == "POST":
        _check_csrf()
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        display_name = request.form.get("display_name", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        errors = _validate_registration(username, email, display_name, password, confirm)
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("quiz/auth/register.html",
                                   username=username, email=email, display_name=display_name)

        db = current_app.db
        QuizUser = current_app.QuizUser
        user = QuizUser(username=username, email=email, display_name=display_name, role="user", active=True)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash("Account created. Please log in.", "success")
        return redirect(url_for("quiz_auth.login"))

    return render_template("quiz/auth/register.html")


@auth_bp.route("/change-password", methods=["GET", "POST"])
@quiz_login_required
def change_password():
    user = get_current_quiz_user()

    if request.method == "POST":
        _check_csrf()
        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")

        if not user.check_password(current_pw):
            flash("Current password is incorrect.", "danger")
        elif len(new_pw) < 8:
            flash("New password must be at least 8 characters.", "danger")
        elif new_pw != confirm:
            flash("New passwords do not match.", "danger")
        else:
            db = current_app.db
            user.set_password(new_pw)
            db.session.commit()
            flash("Password changed successfully.", "success")
            return redirect(url_for("quiz_main.index"))

    return render_template("quiz/auth/change_password.html", user=user)


def _validate_registration(username, email, display_name, password, confirm):
    errors = []
    QuizUser = current_app.QuizUser
    if not username or len(username) < 3:
        errors.append("Username must be at least 3 characters.")
    elif QuizUser.query.filter_by(username=username).first():
        errors.append("Username already taken.")
    if not email or "@" not in email:
        errors.append("A valid email address is required.")
    elif QuizUser.query.filter_by(email=email).first():
        errors.append("Email address already registered.")
    if not display_name:
        errors.append("Display name is required.")
    if len(password) < 8:
        errors.append("Password must be at least 8 characters.")
    if password != confirm:
        errors.append("Passwords do not match.")
    return errors


def _check_csrf():
    """Validate CSRF token stored in session."""
    token = session.get("quiz_csrf_token")
    form_token = request.form.get("csrf_token")
    if not token or token != form_token:
        from flask import abort
        abort(403)


def _safe_redirect(url: str) -> str:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme or parsed.netloc:
        return url_for("quiz_main.index")
    return url
