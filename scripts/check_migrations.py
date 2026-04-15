"""Validate schema/migration health for CI.

If Alembic is configured, run `alembic check` to ensure migrations are in sync.
Always verify SQLAlchemy metadata can be created against a clean SQLite database.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, inspect

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import Base


def _run_alembic_check_if_present() -> None:
    if os.path.exists("alembic.ini") and importlib.util.find_spec("alembic"):
        subprocess.run([sys.executable, "-m", "alembic", "check"], check=True)


def _validate_metadata_creates_tables() -> None:
    # Ensure model classes are imported so metadata is complete.
    import app.models  # noqa: F401

    expected_tables = {"members", "visitors", "care_notes", "prayer_requests", "member_notes"}

    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        engine = create_engine(f"sqlite:///{tmp.name}")
        Base.metadata.create_all(bind=engine)
        existing_tables = set(inspect(engine).get_table_names())

        missing = expected_tables - existing_tables
        if missing:
            raise RuntimeError(f"Schema validation failed; missing tables: {sorted(missing)}")


if __name__ == "__main__":
    _run_alembic_check_if_present()
    _validate_metadata_creates_tables()
    print("Schema and migration checks passed.")
