"""
Database setup for Marge.

Uses SQLite for local development and Postgres for production.
Controlled by the DATABASE_URL environment variable:
  - SQLite:   sqlite:///./marge.db
  - Postgres: postgresql://user:pass@host/dbname
"""

from __future__ import annotations

import os
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./marge.db")

# SQLite needs check_same_thread=False; Postgres does not
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def _to_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _create_all_enabled() -> bool:
    """
    Allow create_all only in explicit local-dev mode.

    Set MARGE_LOCAL_DEV_CREATE_ALL=true to auto-create tables from SQLAlchemy metadata.
    """
    return _to_bool(os.getenv("MARGE_LOCAL_DEV_CREATE_ALL"), default=False)


def _alembic_config() -> Config:
    """Build Alembic config rooted at repository-level alembic.ini."""
    repo_root = Path(__file__).resolve().parents[1]
    config = Config(str(repo_root / "alembic.ini"))
    config.set_main_option("script_location", str(repo_root / "alembic"))
    config.set_main_option("sqlalchemy.url", DATABASE_URL)
    return config


def _assert_migrations_current() -> None:
    """Fail fast when DB schema is not at Alembic head revision."""
    config = _alembic_config()
    script = ScriptDirectory.from_config(config)
    expected_heads = set(script.get_heads())

    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        current_heads = set(context.get_current_heads())

    if current_heads != expected_heads:
        expected = ", ".join(sorted(expected_heads)) or "<none>"
        current = ", ".join(sorted(current_heads)) or "<none>"
        raise RuntimeError(
            "Database schema is not at Alembic head. "
            f"Current revision(s): {current}. Expected: {expected}. "
            "Run migrations before starting production: `alembic upgrade head`."
        )


def get_db():
    """FastAPI dependency: yields a database session and closes it when done."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Initialize DB at startup using migration-aware policy."""
    # Import models so they register with Base
    from app import models  # noqa: F401

    if _create_all_enabled():
        Base.metadata.create_all(bind=engine)
        return

    _assert_migrations_current()
