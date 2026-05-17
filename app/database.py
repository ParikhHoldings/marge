"""
Database setup for Marge.

Uses SQLite for local development and Postgres for production.
Controlled by the DATABASE_URL environment variable:
  - SQLite:   sqlite:///./marge.db
  - Postgres: postgresql://user:pass@host/dbname
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./marge.db")
AUTO_CREATE_SCHEMA = os.getenv("MARGE_AUTO_CREATE_SCHEMA", "true").lower() not in {"0", "false", "no"}

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
    """Create local/dev tables on startup when enabled."""
    if not AUTO_CREATE_SCHEMA:
        return
    # Import models so they register with Base
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_account_scope_columns()


def _ensure_sqlite_account_scope_columns():
    """
    Add account_id columns to existing local SQLite databases.

    SQLAlchemy create_all creates new tables but does not alter existing ones.
    This keeps old local MVP databases usable. New deployments should run the
    Alembic migrations in migrations/ and may disable startup schema creation.
    """
    if not DATABASE_URL.startswith("sqlite"):
        return

    account_scoped_tables = [
        "members",
        "visitors",
        "care_notes",
        "prayer_requests",
        "member_notes",
        "integration_connections",
        "integration_oauth_states",
        "integration_credentials",
        "integration_policies",
        "assistant_actions",
        "assistant_chat_messages",
        "connected_context_items",
        "audit_logs",
    ]
    user_scoped_tables = [
        "integration_oauth_states",
        "integration_credentials",
    ]
    with engine.begin() as conn:
        for table in account_scoped_tables:
            columns = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if not columns:
                continue
            if "account_id" not in columns:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN account_id INTEGER")
            conn.exec_driver_sql(f"CREATE INDEX IF NOT EXISTS ix_{table}_account_id ON {table} (account_id)")
        for table in user_scoped_tables:
            columns = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if not columns:
                continue
            if "user_id" not in columns:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER")
            conn.exec_driver_sql(f"CREATE INDEX IF NOT EXISTS ix_{table}_user_id ON {table} (user_id)")

        columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(assistant_chat_messages)")}
        if columns and "response_json" not in columns:
            conn.exec_driver_sql("ALTER TABLE assistant_chat_messages ADD COLUMN response_json TEXT")

        columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(integration_connections)")}
        if columns and "verified_at" not in columns:
            conn.exec_driver_sql("ALTER TABLE integration_connections ADD COLUMN verified_at DATETIME")
        columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(integration_credentials)")}
        if columns and "verified_at" not in columns:
            conn.exec_driver_sql("ALTER TABLE integration_credentials ADD COLUMN verified_at DATETIME")

        for table in ["pastor_profiles", "account_pastor_profiles"]:
            columns = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if columns and "faith_tradition" not in columns:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN faith_tradition TEXT")

        member_indexes = {
            row[1]: bool(row[2])
            for row in conn.exec_driver_sql("PRAGMA index_list(members)")
        }
        if member_indexes.get("ix_members_rock_id"):
            conn.exec_driver_sql("DROP INDEX IF EXISTS ix_members_rock_id")
        if member_indexes:
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_members_rock_id ON members (rock_id)")
            conn.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS ix_members_account_rock_id ON members (account_id, rock_id)")
