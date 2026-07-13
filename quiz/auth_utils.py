"""Quiz auth helpers — uses Flask session, not Flask-Login, to avoid collision."""
from __future__ import annotations

from functools import wraps
from flask import current_app, redirect, session, url_for, flash


QUIZ_USER_KEY = "quiz_user_id"


def get_current_quiz_user():
    """Return the currently logged-in QuizUser or None."""
    uid = session.get(QUIZ_USER_KEY)
    if not uid:
        return None
    QuizUser = current_app.QuizUser
    return QuizUser.query.get(uid)


def quiz_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get(QUIZ_USER_KEY):
            flash("Please log in to access the quiz.", "info")
            return redirect(url_for("quiz_auth.login"))
        return f(*args, **kwargs)
    return decorated


def quiz_admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        uid = session.get(QUIZ_USER_KEY)
        if not uid:
            flash("Please log in to access the quiz.", "info")
            return redirect(url_for("quiz_auth.login"))
        user = current_app.QuizUser.query.get(uid)
        if not user or not user.is_admin:
            flash("Administrator access required.", "danger")
            return redirect(url_for("quiz_main.index"))
        return f(*args, **kwargs)
    return decorated
