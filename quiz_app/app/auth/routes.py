"""Authentication routes."""
from flask import render_template, redirect, url_for, request, flash, current_app
from flask_login import login_user, logout_user, login_required, current_user
from . import auth_bp
from ..extensions import db
from ..models.user import User
from .forms import LoginForm, RegisterForm, ChangePasswordForm


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Log in an existing user."""
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()

        if user is None or not user.check_password(form.password.data):
            flash("Invalid username or password.", "error")
            return redirect(url_for("auth.login"))

        if not user.active:
            flash("This account has been deactivated.", "error")
            return redirect(url_for("auth.login"))

        login_user(user, remember=form.remember_me.data)
        next_page = request.args.get("next")
        if next_page:
            # Simple URL validation: must be relative and start with /
            if next_page.startswith('/') and not next_page.startswith('//'):
                return redirect(next_page)
        return redirect(url_for("main.dashboard"))

    return render_template("auth/login.html", form=form)


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    """Register a new user."""
    if not current_app.config["REGISTRATION_ENABLED"]:
        flash("Registration is currently disabled.", "warning")
        return redirect(url_for("auth.login"))

    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = RegisterForm()
    if form.validate_on_submit():
        user = User(
            username=form.username.data,
            email=form.email.data,
            display_name=form.display_name.data,
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()

        flash("Account created successfully. You can now log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/register.html", form=form)


@auth_bp.route("/logout")
@login_required
def logout():
    """Log out the current user."""
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    """Change the current user's password."""
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            flash("Current password is incorrect.", "error")
            return redirect(url_for("auth.change_password"))

        current_user.set_password(form.new_password.data)
        db.session.commit()

        flash("Your password has been changed successfully.", "success")
        return redirect(url_for("main.dashboard"))

    return render_template("auth/change_password.html", form=form)
