#!/usr/bin/env python3
"""Run a staging restore drill and emit timing + integrity verification output.

Current implementation supports SQLite URLs for rehearsal in local/staging-like environments.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


CRITICAL_TABLES = [
    "members",
    "visitors",
    "care_notes",
    "prayer_requests",
    "member_notes",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rehearse restore in staging and verify integrity.")
    parser.add_argument(
        "--source-db",
        default=os.getenv("DATABASE_URL", "sqlite:///./marge.db"),
        help="Source database URL (SQLite only).",
    )
    parser.add_argument(
        "--staging-db",
        default="sqlite:///./tmp/staging-restore-drill.db",
        help="Restore target database URL (SQLite only).",
    )
    parser.add_argument(
        "--environment",
        default="pilot",
        choices=["pilot", "production"],
    )
    parser.add_argument(
        "--backup-dir",
        default=os.getenv("DB_BACKUP_DIR", "backups"),
    )
    return parser.parse_args()


def sqlite_path(url: str) -> Path:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise ValueError("Only sqlite URLs are currently supported for rehearsal.")
    return Path(url[len(prefix) :]).resolve()


def table_counts(path: Path) -> dict[str, int]:
    conn = sqlite3.connect(path)
    try:
        counts: dict[str, int] = {}
        for table in CRITICAL_TABLES:
            cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
            counts[table] = int(cur.fetchone()[0])
        return counts
    finally:
        conn.close()


def integrity_ok(path: Path) -> bool:
    conn = sqlite3.connect(path)
    try:
        cur = conn.execute("PRAGMA integrity_check;")
        value = cur.fetchone()[0]
        return value == "ok"
    finally:
        conn.close()


def main() -> int:
    args = parse_args()
    source_path = sqlite_path(args.source_db)
    staging_path = sqlite_path(args.staging_db)

    staging_path.parent.mkdir(parents=True, exist_ok=True)

    start_backup = time.perf_counter()
    backup_cmd = [
        "python3",
        "scripts/db_backup.py",
        "--environment",
        args.environment,
        "--backup-dir",
        args.backup_dir,
        "--database-url",
        args.source_db,
    ]
    backup_proc = subprocess.run(backup_cmd, capture_output=True, text=True)
    backup_elapsed = time.perf_counter() - start_backup
    if backup_proc.returncode != 0:
        raise RuntimeError(backup_proc.stderr.strip())

    backup_line = next((line for line in backup_proc.stdout.splitlines() if line.startswith("backup_created=")), "")
    backup_file = Path(backup_line.replace("backup_created=", "").strip())
    if not backup_file.exists():
        raise FileNotFoundError("Backup file not produced by backup step.")

    if staging_path.exists():
        staging_path.unlink()

    start_restore = time.perf_counter()
    restore_cmd = [
        "python3",
        "scripts/db_restore.py",
        "--backup-file",
        str(backup_file),
        "--database-url",
        args.staging_db,
        "--force",
    ]
    restore_proc = subprocess.run(restore_cmd, capture_output=True, text=True)
    restore_elapsed = time.perf_counter() - start_restore
    if restore_proc.returncode != 0:
        raise RuntimeError(restore_proc.stderr.strip())

    source_counts = table_counts(source_path)
    restored_counts = table_counts(staging_path)
    counts_match = source_counts == restored_counts
    integrity_match = integrity_ok(staging_path)

    payload = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_db": str(source_path),
        "staging_db": str(staging_path),
        "backup_file": str(backup_file),
        "backup_seconds": round(backup_elapsed, 3),
        "restore_seconds": round(restore_elapsed, 3),
        "counts_match": counts_match,
        "integrity_ok": integrity_match,
        "source_counts": source_counts,
        "restored_counts": restored_counts,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))

    if not (counts_match and integrity_match):
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
