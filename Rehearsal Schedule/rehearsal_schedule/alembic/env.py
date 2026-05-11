import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# ensure project root on path so we can import local modules
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# this config is the Alembic Config object
config = context.config

# Interpret the config file for Python logging.
fileConfig(config.config_file_name)

# If the environment provides DATABASE_URL, set it as the sqlalchemy.url
db_url = os.getenv('DATABASE_URL')
if db_url:
    config.set_main_option('sqlalchemy.url', db_url)

# Normalize Heroku-style URLs that start with 'postgres://' to a SQLAlchemy-compatible
# dialect name including the psycopg2 driver: 'postgresql+psycopg2://'
cfg_url = config.get_main_option('sqlalchemy.url')
if cfg_url and cfg_url.startswith('postgres://'):
    cfg_url = cfg_url.replace('postgres://', 'postgresql+psycopg2://', 1)
    config.set_main_option('sqlalchemy.url', cfg_url)

# Now import application DB metadata (after sqlalchemy.url is set)
from db import Base
import models  # noqa: F401  (register models with metadata)

target_metadata = Base.metadata


def run_migrations_offline():
    url = config.get_main_option('sqlalchemy.url')
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
