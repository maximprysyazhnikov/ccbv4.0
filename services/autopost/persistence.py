"""Database persistence functions for autopost."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from utils.settings import get_setting
from utils.db import get_conn

log = logging.getLogger("autopost")


def now_ts() -> int:
    """Get current timestamp."""
    return int(datetime.now(timezone.utc).timestamp())


def seen_recently(conn, user_id: str, symbol: str, timeframe: str, window_sec: int = 90) -> bool:
    """Check if signal was sent recently."""
    now = now_ts()
    row = conn.execute(
        "SELECT 1 FROM autopost_log WHERE user_id=? AND symbol=? AND timeframe=? AND ts>=?",
        (user_id, symbol, timeframe, now - window_sec),
    ).fetchone()
    return bool(row)


def mark_autopost_sent(*, symbol: str, timeframe: str, rr: float | None = None, user_id: str | None = None) -> None:
    """Mark autopost as sent."""
    if user_id is None:
        user_id = get_setting("autopost_user_id", "default") or "default"
    ts = now_ts()
    with get_conn() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(autopost_log)").fetchall()]
        has_rr = "rr" in cols
        has_ts_sent = "ts_sent" in cols
        try:
            if has_rr and has_ts_sent:
                conn.execute(
                    "INSERT INTO autopost_log(user_id,symbol,timeframe,rr,ts_sent,ts) VALUES(?,?,?,?,?,?)",
                    (user_id, symbol, timeframe, float(rr or 0.0), ts, ts),
                )
            elif has_rr:
                conn.execute(
                    "INSERT INTO autopost_log(user_id,symbol,timeframe,ts,rr) VALUES(?,?,?,?,?)",
                    (user_id, symbol, timeframe, ts, float(rr or 0.0)),
                )
            else:
                conn.execute(
                    "INSERT INTO autopost_log(user_id,symbol,timeframe,ts) VALUES(?,?,?,?)",
                    (user_id, symbol, timeframe, ts),
                )
            conn.commit()
        except Exception as e:
            # Ignore unique constraint duplicates or benign insert races; log other errors
            try:
                import sqlite3 as _sqlite
                if isinstance(e, _sqlite.IntegrityError):
                    log.debug("autopost_log insert conflict (ignored): %s", e)
                else:
                    log.warning("autopost_log insert failed: %s", e)
            except Exception:
                log.warning("autopost_log insert failed: %s", e)


def reserve_autopost_send(*, user_id: str, symbol: str, timeframe: str, rr: float | None, window_sec: int) -> bool:
    """Atomically reserve autopost slot."""
    now = now_ts()
    with get_conn() as conn:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT 1 FROM autopost_log WHERE user_id=? AND symbol=? AND timeframe=? AND ts>=?",
            (user_id, symbol, timeframe, now - window_sec),
        ).fetchone()
        if row:
            return False
        
        cols = [r[1] for r in cur.execute("PRAGMA table_info(autopost_log)").fetchall()]
        has_rr = "rr" in cols
        has_ts_sent = "ts_sent" in cols
        
        if has_rr and has_ts_sent:
            cur.execute(
                "INSERT INTO autopost_log(user_id,symbol,timeframe,ts,rr,ts_sent) VALUES(?,?,?,?,?,?)",
                (user_id, symbol, timeframe, now, float(rr or 0.0), now),
            )
        elif has_ts_sent:
            cur.execute(
                "INSERT INTO autopost_log(user_id,symbol,timeframe,ts,ts_sent) VALUES(?,?,?,?,?)",
                (user_id, symbol, timeframe, now, now),
            )
        elif has_rr:
            cur.execute(
                "INSERT INTO autopost_log(user_id,symbol,timeframe,ts,rr) VALUES(?,?,?,?,?)",
                (user_id, symbol, timeframe, now, float(rr or 0.0)),
            )
        else:
            cur.execute(
                "INSERT INTO autopost_log(user_id,symbol,timeframe,ts) VALUES(?,?,?,?)",
                (user_id, symbol, timeframe, now),
            )
        
        conn.commit()
        return True


def complete_autopost_send(*, user_id: str, symbol: str, timeframe: str, rr: float | None) -> None:
    """Complete autopost send by updating ts_sent."""
    now = now_ts()
    with get_conn() as conn:
        cur = conn.cursor()
        cols = [r[1] for r in cur.execute("PRAGMA table_info(autopost_log)").fetchall()]
        has_ts_sent = "ts_sent" in cols
        has_rr = "rr" in cols
        if has_ts_sent:
            try:
                cur.execute(
                    """
                    UPDATE autopost_log
                    SET ts_sent=?, rr=COALESCE(?, rr)
                    WHERE rowid IN (
                        SELECT rowid FROM autopost_log
                        WHERE user_id=? AND symbol=? AND timeframe=?
                        ORDER BY ts DESC
                        LIMIT 1
                    )
                    """,
                    (now, float(rr or 0.0) if has_rr else None, user_id, symbol, timeframe),
                )
            except Exception:
                cur.execute(
                    """
                    UPDATE autopost_log
                    SET ts_sent=?
                    WHERE rowid IN (
                        SELECT rowid FROM autopost_log
                        WHERE user_id=? AND symbol=? AND timeframe=?
                        ORDER BY ts DESC
                        LIMIT 1
                    )
                    """,
                    (now, user_id, symbol, timeframe),
                )
            conn.commit()


# ──────────────────────────────────────────────────────────────────────────────
# Candidate persistence for stable-checks
# ──────────────────────────────────────────────────────────────────────────────

def ensure_autopost_candidates_table() -> None:
    """Create autopost_candidates table if not exists."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS autopost_candidates (
                user_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                consecutive_passes INTEGER DEFAULT 0,
                last_pass_ts INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, symbol, timeframe)
            )
            """
        )
        conn.commit()


def record_candidate_pass(*, user_id: str, symbol: str, timeframe: str) -> int:
    """Increment consecutive_passes for (user,symbol,tf) and return new value."""
    now = now_ts()
    ensure_autopost_candidates_table()
    with get_conn() as conn:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT consecutive_passes FROM autopost_candidates WHERE user_id=? AND symbol=? AND timeframe=?",
            (user_id, symbol, timeframe),
        ).fetchone()
        if row:
            cur.execute(
                "UPDATE autopost_candidates SET consecutive_passes=consecutive_passes+1, last_pass_ts=? WHERE user_id=? AND symbol=? AND timeframe=?",
                (now, user_id, symbol, timeframe),
            )
            conn.commit()
            return int(row[0]) + 1
        else:
            cur.execute(
                "INSERT INTO autopost_candidates(user_id,symbol,timeframe,consecutive_passes,last_pass_ts) VALUES(?,?,?,?,?)",
                (user_id, symbol, timeframe, 1, now),
            )
            conn.commit()
            return 1


def reset_candidate_pass(*, user_id: str, symbol: str, timeframe: str) -> None:
    """Reset consecutive_passes to 0 for candidate."""
    ensure_autopost_candidates_table()
    with get_conn() as conn:
        conn.execute(
            "UPDATE autopost_candidates SET consecutive_passes=0, last_pass_ts=? WHERE user_id=? AND symbol=? AND timeframe=?",
            (now_ts(), user_id, symbol, timeframe),
        )
        conn.commit()


def get_candidate_passes(*, user_id: str, symbol: str, timeframe: str) -> int:
    """Return consecutive_passes for candidate or 0."""
    ensure_autopost_candidates_table()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT consecutive_passes FROM autopost_candidates WHERE user_id=? AND symbol=? AND timeframe=?",
            (user_id, symbol, timeframe),
        ).fetchone()
        return int(row[0]) if row else 0
