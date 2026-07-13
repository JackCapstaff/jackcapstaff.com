"""Testing blueprint."""
from flask import Blueprint

testing_bp = Blueprint(
    "testing",
    __name__,
    template_folder="../templates",
    static_folder="../static",
)

from . import routes  # noqa: E402, F401
