"""KPI handlers (/daily_now, /winrate_now)."""
from __future__ import annotations

import logging
import time
import sqlite3
import os
from telegram import Update
from telegram.ext import ContextTypes

from services.daily_tracker import compute_daily_summary
from telegram_bot.handlers.helpers import _send

log = logging.getLogger("tg.handlers")

_DB_PATH = (
    os.getenv("DB_PATH")
    or os.getenv("SQLITE_PATH")
    or os.getenv("DATABASE_PATH")
    or "storage/bot.db"
)


def _conn_local():
    """Get local database connection."""
    os.makedirs(os.path.dirname(_DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


async def daily_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /daily_now command."""
    uid = update.effective_user.id
    try:
        metrics, md = compute_daily_summary(uid)
        await _send(update, context, md, parse_mode="Markdown")
    except Exception as e:
        await _send(update, context, f"⚠️ daily_now error: {e}")


async def winrate_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /winrate_now command."""
    uid = update.effective_user.id
    days = 7
    if context.args:
        try:
            days = max(1, int(context.args[0]))
        except Exception:
            pass
    try:
        with _conn_local() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COALESCE(rr_threshold,1.5) FROM user_settings WHERE user_id=?", (uid,))
            row = cur.fetchone()
            rr_min = float(row[0] if row else 1.5)
            t1 = int(time.time())
            t0 = t1 - days * 86400
            cur.execute("""
                SELECT status, rr, pnl_pct FROM signals
                WHERE user_id=? AND status IN ('WIN','LOSS')
                  AND COALESCE(rr,0) >= ? AND COALESCE(ts_closed, ts_created) BETWEEN ? AND ?
            """, (uid, rr_min, t0, t1))
            rows = cur.fetchall()
        wins = sum(1 for r in rows if r["status"] == "WIN")
        n = len(rows)
        winrate = (wins / n * 100.0) if n else 0.0
        avg_rr = (sum(float(r["rr"]) for r in rows) / n) if n else 0.0
        avg_pnl = (sum(float(r["pnl_pct"] or 0.0) for r in rows) / n) if n else 0.0
        md = (
            f"**📈 Winrate {days}d (RR≥{rr_min:g})**\n\n"
            f"Trades: **{n}** | WIN: **{wins}** | LOSS: **{n-wins}** | Winrate: **{winrate:.2f}%**\n"
            f"Avg RR: **{avg_rr:.2f}** | Avg PnL: **{avg_pnl:.2f}%**"
        )
        await _send(update, context, md, parse_mode="Markdown")
    except Exception as e:
        await _send(update, context, f"⚠️ winrate_now error: {e}")
