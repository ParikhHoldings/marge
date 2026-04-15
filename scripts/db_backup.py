#!/usr/bin/env python3
"""Create timestamped database backups and enforce retention policy.

Supports:
- SQLite: file copy
- PostgreSQL: pg_dump custom format
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()


@dataclass
class RetentionPolicy:
    days: int
    keep_min: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create DB backup and enforce retention.")
    parser.add_argument(
        "--environment",
        choices=["pilot", "production"],
        default=os.getenv("DEPLOY_ENV", "pilot"),
        help="Target environment (drives retention defaults and output directory).",
    )
    parser.add_argument(
        "--backup-dir",
        default=os.getenv("DB_BACKUP_DIR", "backups"),
        help="Root backup directory.",
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL", "sqlite:///./marge.db"),
        help="Database URL override.",
    )
    return parser.parse_args()


def retention_for(environment: str) -> RetentionPolicy:
    if environment == "production":
        days = int(os.getenv("DB_BACKUP_RETENTION_DAYS_PRODUCTION", "35"))
    else:
        days = int(os.getenv("DB_BACKUP_RETENTION_DAYS_PILOT", "14"))
    keep_min = int(os.getenv("DB_BACKUP_KEEP_MIN", "10"))
    return RetentionPolicy(days=days, keep_min=keep_min)


def sqlite_path_from_url(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("Not a SQLite URL")
    raw_path = database_url[len(prefix) :]
    return Path(raw_path).resolve()


def backup_sqlite(database_url: str, backup_path: Path) -> None:
    source_path = sqlite_path_from_url(database_url)
    if not source_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {source_path}")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, backup_path)


def backup_postgres(database_url: str, backup_path: Path) -> None:
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["pg_dump", "--format=custom", "--file", str(backup_path), database_url],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pg_dump failed: {result.stderr.strip()}")


def build_backup_path(base_dir: Path, environment: str, database_url: str) -> Path:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if database_url.startswith("sqlite"):
        ext = "sqlite3"
    else:
        ext = "dump"
    return base_dir / environment / f"marge-{environment}-{ts}.{ext}"


def apply_retention(backup_dir: Path, policy: RetentionPolicy) -> list[Path]:
    files = sorted([p for p in backup_dir.glob("marge-*.*") if p.is_file()], key=lambda p: p.stat().st_mtime, reverse=True)
    cutoff = datetime.now(UTC) - timedelta(days=policy.days)

    to_delete: list[Path] = []
    for idx, backup in enumerate(files):
        modified_at = datetime.fromtimestamp(backup.stat().st_mtime, tz=UTC)
        if idx >= policy.keep_min and modified_at < cutoff:
            to_delete.append(backup)

    for path in to_delete:
        path.unlink(missing_ok=True)

    return to_delete


def backup_engine(database_url: str) -> str:
    return urlparse(database_url).scheme


def main() -> int:
    args = parse_args()
    base_dir = Path(args.backup_dir).resolve()
    env = args.environment
    database_url = args.database_url
    target = build_backup_path(base_dir, env, database_url)

    try:
        engine = backup_engine(database_url)
        if engine.startswith("sqlite"):
            backup_sqlite(database_url, target)
        elif engine.startswith("postgres"):
            backup_postgres(database_url, target)
        else:
            raise ValueError(f"Unsupported DB engine: {engine}")

        policy = retention_for(env)
        deleted = apply_retention(target.parent, policy)

        print(f"backup_created={target}")
        print(f"retention_days={policy.days} keep_min={policy.keep_min}")
        print(f"deleted_old_backups={len(deleted)}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"backup_failed={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
