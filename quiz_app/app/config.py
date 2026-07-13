"""Application configuration classes."""
import os
from datetime import timedelta


class BaseConfig:
    """Shared defaults for all environments."""

    SECRET_KEY: str = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-prod")
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False
    WTF_CSRF_ENABLED: bool = True
    WTF_CSRF_TIME_LIMIT: int = 3600  # 1 hour

    # Upload
    MAX_CONTENT_LENGTH: int = int(os.environ.get("MAX_CONTENT_LENGTH", 10 * 1024 * 1024))

    # Auth
    REGISTRATION_ENABLED: bool = os.environ.get("REGISTRATION_ENABLED", "true").lower() == "true"
    SESSION_COOKIE_HTTPONLY: bool = True
    REMEMBER_COOKIE_HTTPONLY: bool = True

    # Test display
    TOPIC_VISIBLE_DURING_TEST: bool = (
        os.environ.get("TOPIC_VISIBLE_DURING_TEST", "true").lower() == "true"
    )

    # Time limits (minutes)
    MIN_TIME_LIMIT_MINUTES: int = int(os.environ.get("MIN_TIME_LIMIT_MINUTES", 1))
    MAX_TIME_LIMIT_MINUTES: int = int(os.environ.get("MAX_TIME_LIMIT_MINUTES", 480))

    # Adaptive settings
    MIN_ADAPTIVE_ATTEMPTS: int = int(os.environ.get("MIN_ADAPTIVE_ATTEMPTS", 10))
    ADAPTIVE_RECENT_ATTEMPTS: int = int(os.environ.get("ADAPTIVE_RECENT_ATTEMPTS", 30))
    ADAPTIVE_STRENGTH: float = float(os.environ.get("ADAPTIVE_STRENGTH", 1.5))
    ADAPTIVE_MAX_TOPIC_SHARE: float = float(os.environ.get("ADAPTIVE_MAX_TOPIC_SHARE", 0.5))

    # Staged import expiry
    STAGED_IMPORT_EXPIRY_MINUTES: int = int(os.environ.get("STAGED_IMPORT_EXPIRY_MINUTES", 60))

    # Timer warning thresholds (seconds)
    TIMER_WARNING_THRESHOLDS: list = [300, 60, 30]


class DevelopmentConfig(BaseConfig):
    DEBUG: bool = True
    SQLALCHEMY_DATABASE_URI: str = os.environ.get(
        "DATABASE_URL", "sqlite:///quiz.db"
    )
    SESSION_COOKIE_SECURE: bool = False
    REMEMBER_COOKIE_SECURE: bool = False


class TestingConfig(BaseConfig):
    TESTING: bool = True
    WTF_CSRF_ENABLED: bool = False
    SQLALCHEMY_DATABASE_URI: str = "sqlite:///:memory:"
    SESSION_COOKIE_SECURE: bool = False
    REMEMBER_COOKIE_SECURE: bool = False
    STAGED_IMPORT_EXPIRY_MINUTES: int = 5
    MIN_ADAPTIVE_ATTEMPTS: int = 3


class ProductionConfig(BaseConfig):
    DEBUG: bool = False
    SQLALCHEMY_DATABASE_URI: str = os.environ.get("DATABASE_URL", "sqlite:///quiz.db")
    SESSION_COOKIE_SECURE: bool = True
    REMEMBER_COOKIE_SECURE: bool = True
    SESSION_COOKIE_SAMESITE: str = "Lax"


config_map: dict = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
