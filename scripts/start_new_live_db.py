from __future__ import annotations

import argparse
import os
import sqlite3
import time
from pathlib import Path


DEFAULT_SOURCE = Path("storage") / "bot.db"
DEFAULT_TARGET = Path("storage") / "bot_live_v2.db"

LIVE_HISTORY_TABLES = (
    "trades",
    "signals",
    "paper_signals",
    "decision_log",
    "autopost_log",
)

STATE_TABLES = (
    "autopost_candidates",
)


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return bool(row)


def qname(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def copy_database(source: Path, target: Path, replace: bool) -> None:
    if not source.exists():
        raise FileNotFoundError(f"source DB not found: {source}")
    if target.exists() and not replace:
        raise FileExistsError(f"target DB already exists: {target}")

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()

    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def archive_and_reset(conn: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}

    for table in LIVE_HISTORY_TABLES:
        if not table_exists(conn, table):
            continue
        archive = f"{table}_archive"
        conn.execute(f"DROP TABLE IF EXISTS {qname(archive)}")
        conn.execute(f"CREATE TABLE {qname(archive)} AS SELECT * FROM {qname(table)}")
        count = int(conn.execute(f"SELECT COUNT(*) FROM {qname(archive)}").fetchone()[0] or 0)
        counts[archive] = count
        conn.execute(f"DELETE FROM {qname(table)}")
        conn.execute("DELETE FROM sqlite_sequence WHERE name=?", (table,))

    for table in STATE_TABLES:
        if table_exists(conn, table):
            counts[f"{table}_reset"] = int(
                conn.execute(f"SELECT COUNT(*) FROM {qname(table)}").fetchone()[0] or 0
            )
            conn.execute(f"DELETE FROM {qname(table)}")

    now = str(int(time.time()))
    if table_exists(conn, "settings"):
        conn.execute(
            """
            INSERT INTO settings(key, value)
            VALUES('autopost_perf_epoch_ts', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (now,),
        )
        conn.execute("DELETE FROM settings WHERE key='allowlist_proposal_last_ready_key'")

    if table_exists(conn, "decision_log"):
        conn.execute(
            """
            INSERT INTO decision_log(source, decision, reason, risk_state)
            VALUES('migration', 'NEW_LIVE_DB_STARTED', ?, 'RECOVERY_WARMUP')
            """,
            (f"archived old history; performance epoch reset to {now}",),
        )

    conn.commit()
    return counts


def print_summary(target: Path, counts: dict[str, int]) -> None:
    print(f"new live DB: {target}")
    for name in sorted(counts):
        print(f"{name}: {counts[name]}")

    with sqlite3.connect(target) as conn:
        print("live table counts:")
        for table in LIVE_HISTORY_TABLES:
            if table_exists(conn, table):
                count = int(conn.execute(f"SELECT COUNT(*) FROM {qname(table)}").fetchone()[0] or 0)
                print(f"{table}: {count}")
        if table_exists(conn, "settings"):
            row = conn.execute(
                "SELECT value FROM settings WHERE key='autopost_perf_epoch_ts'"
            ).fetchone()
            print(f"autopost_perf_epoch_ts: {row[0] if row else '-'}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a fresh live DB with old trading history archived in *_archive tables."
    )
    parser.add_argument("--source", default=os.getenv("SOURCE_DB", str(DEFAULT_SOURCE)))
    parser.add_argument("--target", default=os.getenv("TARGET_DB", str(DEFAULT_TARGET)))
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    source = Path(args.source)
    target = Path(args.target)

    copy_database(source, target, replace=args.replace)
    with sqlite3.connect(target) as conn:
        conn.row_factory = sqlite3.Row
        counts = archive_and_reset(conn)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    print_summary(target, counts)


if __name__ == "__main__":
    main()
