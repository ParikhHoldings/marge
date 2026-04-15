#!/usr/bin/env python3
"""Restore Marge database from a backup artifact."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Restore DB from backup artifact.")
    parser.add_argument("--backup-file", required=True, help="Path to backup artifact.")
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", "sqlite:///./marge.db"),
        help="Target database URL.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow replacing existing SQLite database file.",
    )
    return parser.parse_args()


def sqlite_path_from_url(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("Not a SQLite URL")
    return Path(database_url[len(prefix) :]).resolve()


def restore_sqlite(backup_file: Path, database_url: str, force: bool) -> None:
    target = sqlite_path_from_url(database_url)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not force:
        raise FileExistsError(f"Target exists, rerun with --force: {target}")
    shutil.copy2(backup_file, target)


def restore_postgres(backup_file: Path, database_url: str) -> None:
    result = subprocess.run(
        [
            "pg_restore",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            "--dbname",
            database_url,
            str(backup_file),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pg_restore failed: {result.stderr.strip()}")


def main() -> int:
    args = parse_args()
    backup_file = Path(args.backup_file).resolve()
    database_url = args.database_url

    try:
        if not backup_file.exists():
            raise FileNotFoundError(f"Backup file not found: {backup_file}")

        scheme = urlparse(database_url).scheme
        if scheme.startswith("sqlite"):
            restore_sqlite(backup_file, database_url, args.force)
        elif scheme.startswith("postgres"):
            restore_postgres(backup_file, database_url)
        else:
            raise ValueError(f"Unsupported DB engine: {scheme}")

        print(f"restore_completed backup={backup_file} target={database_url}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"restore_failed={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
