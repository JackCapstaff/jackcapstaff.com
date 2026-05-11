from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import os
import os.path

# Optionally load a .env file in project root for local debugging. This is
# a small, dependency-free loader: create a file named `.env` in the project
# root with lines like `DATABASE_URL=...` and `DB_ECHO=1` and it will be
# read when this module imports. Existing environment variables are not
# overwritten.
try:
	root = os.path.dirname(os.path.dirname(__file__))
	env_path = os.path.join(root, ".env")
	if os.path.exists(env_path):
		with open(env_path, "r", encoding="utf-8") as f:
			for ln in f:
				ln = ln.strip()
				if not ln or ln.startswith("#") or "=" not in ln:
					continue
				k, v = ln.split("=", 1)
				k = k.strip()
				v = v.strip().strip('"').strip("'")
				if k and k not in os.environ:
					os.environ[k] = v
except Exception:
	# If .env parsing fails, proceed without blocking imports (import-safe)
	pass

# Read DATABASE_URL from environment; allow import-time without a DB URL
DATABASE_URL = os.environ.get("DATABASE_URL")

# Allow a developer flag to print SQL to stdout for debugging: set DB_ECHO=1
DB_ECHO = os.environ.get("DB_ECHO", "0") in ("1", "true", "True")

# Heroku historically provides URLs that start with `postgres://`.
# Modern SQLAlchemy expects the `postgresql` dialect name and a driver, e.g.
# `postgresql+psycopg2://`. Normalize common Heroku-style URLs here.
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
	DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg2://", 1)

if DATABASE_URL:
	engine = create_engine(DATABASE_URL, pool_pre_ping=True, echo=DB_ECHO)
	SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
else:
	engine = None
	SessionLocal = None

# Declarative base is always available for Alembic autogenerate
Base = declarative_base()


def get_session():
	"""Return a new DB session or raise a clear error if the DB is not configured."""
	if SessionLocal is None:
		raise RuntimeError("DATABASE_URL is not set; cannot create DB session. Set DATABASE_URL and retry.")
	return SessionLocal()