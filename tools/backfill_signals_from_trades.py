
"""Backfill signals table from recent trades.

Usage:
  DB_PATH=storage/bot.db python tools/backfill_signals_from_trades.py --days 7
"""
import os
import sqlite3
import time
from datetime import datetime

DB = os.getenv("DB_PATH") or "storage/bot.db"
DEFAULT_USER = int(os.getenv("TELEGRAM_CHAT_ID") or 0)

def backfill_signals(days=7, dry=False):
    DB = os.getenv("DB_PATH") or "storage/bot.db"
    DEFAULT_USER = int(os.getenv("TELEGRAM_CHAT_ID") or 0)
    since = int(time.time()) - days * 86400
    con = sqlite3.connect(DB)
    cur = con.cursor()
    # Ensure signals table exists
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='signals'")
    if not cur.fetchone():
        cur.execute('''CREATE TABLE IF NOT EXISTS signals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, symbol TEXT, tf TEXT, direction TEXT,
            entry REAL, sl REAL, tp REAL, rr REAL,
            status TEXT, pnl_pct REAL, pnl_usd REAL,
            closed_at TEXT, ts_created INTEGER, ts_closed INTEGER
        )''')
        con.commit()
    # Find closed trades in timeframe
    cols = [r[1] for r in cur.execute("PRAGMA table_info(trades)").fetchall()]
    # Prefer pnl_usd, then pnl, then 0
    if 'pnl_usd' in cols:
        pnl_usd_expr = 'pnl_usd'
    elif 'pnl' in cols:
        pnl_usd_expr = 'pnl'
    else:
        pnl_usd_expr = '0'
    q = f"""
    SELECT id, symbol, timeframe, direction, entry, sl, tp, rr_realized, rr_planned, 0 as pnl_pct, COALESCE({pnl_usd_expr}, 0) as pnl_usd, closed_at, opened_at
    FROM trades
    WHERE (status='CLOSED' OR status='WIN' OR status='LOSS') AND CAST(closed_at AS INTEGER) >= ?
    """
    rows = cur.execute(q, (since,)).fetchall()
    inserted = 0
    def _parse_ts(v):
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return int(v)
        try:
            return int(v)
        except Exception:
            try:
                dt = datetime.fromisoformat(str(v))
                return int(dt.timestamp())
            except Exception:
                return None

    for r in rows:
        tid, symbol, tf, direction, entry, sl, tp, rr_realized, rr_planned, pnl_pct, pnl_usd, closed_at, opened_at = r
        ts_closed = _parse_ts(closed_at)
        ts_created = _parse_ts(opened_at) if opened_at else (ts_closed - 60 if ts_closed else int(time.time()))
        rr = rr_realized or rr_planned or None
        # avoid duplicates by same symbol+ts_closed
        chk = cur.execute("SELECT 1 FROM signals WHERE symbol=? AND ts_closed=? LIMIT 1", (symbol, ts_closed)).fetchone()
        if chk:
            continue
        if dry:
            print(f"DRY: would insert signal for {symbol} closed_at={ts_closed} rr={rr} pnl={pnl_usd}")
            inserted += 1
            continue
        cur.execute(
            "INSERT INTO signals(user_id,symbol,timeframe,direction,entry,sl,tp,rr,status,pnl_pct,pnl_usd,closed_at,ts_created,ts_closed) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (DEFAULT_USER, symbol, tf or '15m', direction, entry, sl, tp, rr, 'CLOSED', 0, pnl_usd, str(ts_closed) if ts_closed else None, ts_created, ts_closed),
        )
        inserted += 1
    if not dry:
        con.commit()
    con.close()
    return inserted

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()
    print(f"[backfill] DB={DB} since={args.days}d ({int(time.time()) - args.days * 86400}) dry={args.dry})")
    inserted = backfill_signals(args.days, args.dry)
    print(f"Done. inserted={inserted}")

def _parse_ts(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    try:
        return int(v)
    except Exception:
        try:
            dt = datetime.fromisoformat(str(v))
            return int(dt.timestamp())
        except Exception:
            return None

