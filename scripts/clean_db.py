#!/usr/bin/env python3
"""Safe DB cleanup utility for ccbv3.8

Features:
- Creates timestamped backups of the SQLite DB (default: storage/bot.db)
- Supports deleting specific tables (`--tables`)
- Supports full DB reset (`--drop-all`) which removes the DB file
- `--dry-run` to preview actions
- `--force` to skip interactive confirmation

Usage examples:
  python scripts/clean_db.py --backup --drop-all --force
  python scripts/clean_db.py --tables autopost_candidates,autopost_log --backup
"""

from __future__ import annotations
import argparse
import datetime
import logging
import os
import shutil
import sqlite3
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
LOG = logging.getLogger("clean_db")

DEFAULT_DB = os.getenv("DB_PATH", "storage/bot.db")


def backup_db(db_path: str) -> str:
    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dest = f"{db_path}.bak.{ts}"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(db_path, dest)
    LOG.info(f"Backup created: {dest}")
    return dest


def delete_db_file(db_path: str) -> None:
    os.remove(db_path)
    LOG.info(f"Deleted DB file: {db_path}")


def delete_tables(db_path: str, tables: list[str]) -> None:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        LOG.info("Disabling foreign keys temporarily")
        cur.execute("PRAGMA foreign_keys = OFF;")
        for t in tables:
            LOG.info(f"Deleting rows from table: {t}")
            cur.execute(f"DELETE FROM {t};")
        conn.commit()
        LOG.info("Deleted requested tables; running VACUUM")
        cur.execute("VACUUM;")
    finally:
        conn.close()


def confirm(prompt: str) -> bool:
    ans = input(prompt + " [y/N]: ").strip().lower()
    return ans in ("y", "yes")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safe DB cleanup for ccbv3.8")
    parser.add_argument("--db", default=DEFAULT_DB, help="Path to SQLite DB (default: %(default)s)")
    parser.add_argument("--backup", action="store_true", help="Create timestamped backup before changes")
    parser.add_argument("--drop-all", dest="drop_all", action="store_true", help="Remove DB file completely (full reset)")
    parser.add_argument("--tables", help="Comma-separated list of tables to DELETE FROM (e.g. autopost_log,autopost_candidates)")
    parser.add_argument("--dry-run", action="store_true", help="Show actions but do not modify anything")
    parser.add_argument("--force", action="store_true", help="Don't ask for confirmation")

    args = parser.parse_args(argv)
    db = args.db

    LOG.info(f"DB path: {db}")

    if not os.path.exists(db):
        LOG.warning(f"DB does not exist: {db}")
        return 1

    actions = []
    if args.backup:
        actions.append(f"backup -> will copy '{db}' to '{db}.bak.<ts>'")
    if args.drop_all:
        actions.append(f"drop-all -> will remove file '{db}'")
    if args.tables:
        tables = [t.strip() for t in args.tables.split(",") if t.strip()]
        actions.append(f"delete tables -> {tables}")
    if not actions:
        LOG.error("No action requested. Provide --drop-all or --tables or --backup")
        parser.print_help()
        return 2

    LOG.info("Planned actions:")
    for a in actions:
        LOG.info(f"  - {a}")

    if args.dry_run:
        LOG.info("Dry run enabled; no changes will be made.")
        return 0

    if not args.force:
        ok = confirm("Proceed with the above actions?")
        if not ok:
            LOG.info("Aborted by user")
            return 0

    # Execute
    if args.backup:
        backup_db(db)

    if args.drop_all:
        delete_db_file(db)
        LOG.info("Full DB removed. You can restart the app and migrations will recreate the DB.")
        return 0

    if args.tables:
        delete_tables(db, tables)

    LOG.info("Done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
