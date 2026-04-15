"""
Database setup for Marge.

Uses SQLite for local development and Postgres for production.
Controlled by the DATABASE_URL environment variable:
  - SQLite:   sqlite:///./marge.db
  - Postgres: postgresql://user:pass@host/dbname
"""

import os
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./marge.db")

# SQLite needs check_same_thread=False; Postgres does not
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """FastAPI dependency: yields a database session and closes it when done."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables. Called on app startup."""
    # Import models so they register with Base
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _apply_runtime_migrations()


def _apply_runtime_migrations() -> None:
    """
    Lightweight startup migration for church_id columns.

    This keeps local/staging environments functional without requiring Alembic.
    """
    default_church_id = os.getenv("DEFAULT_CHURCH_ID", "default-church")
    tables = ["members", "visitors", "care_notes", "prayer_requests", "member_notes"]
    inspector = inspect(engine)
    dialect = engine.dialect.name

    with engine.begin() as connection:
        for table in tables:
            columns = {col["name"] for col in inspector.get_columns(table)}
            if "church_id" in columns:
                continue

            if dialect == "sqlite":
                connection.execute(
                    text(
                        f"ALTER TABLE {table} "
                        "ADD COLUMN church_id VARCHAR NOT NULL DEFAULT :church_id"
                    ),
                    {"church_id": default_church_id},
                )
            else:
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN church_id VARCHAR"))
                connection.execute(
                    text(f"UPDATE {table} SET church_id = :church_id WHERE church_id IS NULL"),
                    {"church_id": default_church_id},
                )
                connection.execute(text(f"ALTER TABLE {table} ALTER COLUMN church_id SET NOT NULL"))

            connection.execute(text(f"CREATE INDEX IF NOT EXISTS ix_{table}_church_id ON {table} (church_id)"))
