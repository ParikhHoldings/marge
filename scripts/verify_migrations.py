#!/usr/bin/env python3
"""
Verify Alembic migrations against a clean temporary SQLite database.

This is a local guardrail for schema work. It runs the migration chain, compares
the resulting table/column set with SQLAlchemy metadata, and asks Alembic to
check for autogenerate drift.

Usage:
  .venv/bin/python scripts/verify_migrations.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, inspect


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run_alembic(args: list[str], database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env, check=True)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="marge-migrations-") as tmp_dir:
        db_path = Path(tmp_dir) / "marge.db"
        database_url = f"sqlite:///{db_path}"

        run_alembic(["upgrade", "head"], database_url)

        os.environ["DATABASE_URL"] = database_url
        from app.database import Base  # noqa: WPS433
        from app import models  # noqa: F401,WPS433

        engine = create_engine(database_url)
        inspector = inspect(engine)
        actual_tables = set(inspector.get_table_names()) - {"alembic_version"}
        expected_tables = set(Base.metadata.tables)

        missing_tables = expected_tables - actual_tables
        extra_tables = actual_tables - expected_tables
        column_diffs: dict[str, dict[str, list[str]]] = {}
        for table_name, table in Base.metadata.tables.items():
            if table_name not in actual_tables:
                continue
            actual_columns = {column["name"] for column in inspector.get_columns(table_name)}
            expected_columns = set(table.columns.keys())
            if actual_columns != expected_columns:
                column_diffs[table_name] = {
                    "missing": sorted(expected_columns - actual_columns),
                    "extra": sorted(actual_columns - expected_columns),
                }

        if missing_tables or extra_tables or column_diffs:
            print("Migration schema does not match SQLAlchemy metadata.", file=sys.stderr)
            print({"missing_tables": sorted(missing_tables), "extra_tables": sorted(extra_tables), "column_diffs": column_diffs}, file=sys.stderr)
            return 1

        run_alembic(["check"], database_url)
        print(f"Migration schema verified: {len(expected_tables)} tables at Alembic head.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
