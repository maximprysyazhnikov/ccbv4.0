# services/daily_tracker.py
from __future__ import annotations

import os
import sqlite3
import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from typing import List, Optional, Tuple

log = logging.getLogger("daily_tracker")

DB_PATH = os.getenv("DB_PATH") or os.getenv("SQLITE_PATH") or os.getenv("DATABASE_PATH") or "storage/bot.db"
TZ = ZoneInfo(os.getenv("TZ_NAME", "Europe/Kyiv"))

# ---------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------
def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c

def _get_setting(key: str, default: str) -> str:
    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute("SELECT value FROM settings WHERE key=?", (key,))
            row = cur.fetchone()
            if row and row[0] is not None:
                return str(row[0])
    except Exception as e:
        log.debug("get_setting(%s) failed: %s", key, e)
    # .env fallback
    env_val = os.getenv(key.upper())
    return env_val if env_val is not None else default

# ---------------------------------------------------------------------
# Domain
# ---------------------------------------------------------------------
@dataclass
class TradeRow:
    symbol: str
    timeframe: Optional[str]
    status: str
    pnl: Optional[float]
    rr: Optional[float]
    closed_at: Optional[str]
    trade_mode: Optional[str] = None  # 'standard' or 'scalping'
    close_reason: Optional[str] = None

def _bounds_for_day_kyiv(dt: datetime) -> Tuple[str, str, str]:
    """(label_date, start_iso, end_iso) для конкретної дати в Europe/Kyiv."""
    start = datetime.combine(dt.date(), time(0, 0, 0), tzinfo=TZ)
    end = start + timedelta(days=1)
    fmt = "%Y-%m-%d %H:%M:%S"
    return (dt.date().isoformat(), start.strftime(fmt), end.strftime(fmt))

def _bounds_for_range_days(period_days: int) -> Tuple[str, str]:
    """(start_iso, end_iso) для інтервалу [now - period_days, now) у Europe/Kyiv."""
    now = datetime.now(TZ)
    start = now - timedelta(days=int(period_days))
    fmt = "%Y-%m-%d %H:%M:%S"
    return (start.strftime(fmt), now.strftime(fmt))

def _fetch_trades_closed_between(start_iso: str, end_iso: str) -> List[TradeRow]:
    rows: List[TradeRow] = []
    # Конвертуємо ISO-дату у Unix timestamp для порівняння
    import time as _time
    from datetime import datetime as _dt
    try:
        start_ts = int(_dt.strptime(start_iso, "%Y-%m-%d %H:%M:%S").timestamp())
        end_ts = int(_dt.strptime(end_iso, "%Y-%m-%d %H:%M:%S").timestamp())
    except Exception:
        start_ts, end_ts = 0, int(_time.time()) + 86400
    
    with _conn() as c:
        cur = c.cursor()
        # основний шлях: фільтруємо по closed_at (підтримка числових timestamp і ISO-дат)
        cur.execute(
            """
            SELECT symbol, timeframe, status, 
                   COALESCE(pnl_usd, pnl) as pnl, 
                   COALESCE(rr_realized, rr) as rr, 
                   closed_at,
                   trade_mode,
                   COALESCE(close_reason, reason_close) as close_reason
            FROM trades
            WHERE status IN ('WIN','LOSS','CLOSED','TP','SL')
              AND closed_at IS NOT NULL
              AND (
                  (typeof(closed_at) = 'integer' AND closed_at >= ? AND closed_at < ?)
                  OR (typeof(closed_at) = 'text' AND (
                      (closed_at GLOB '[0-9]*' AND CAST(closed_at AS INTEGER) >= ? AND CAST(closed_at AS INTEGER) < ?)
                      OR (closed_at >= ? AND closed_at < ?)
                  ))
              )
            ORDER BY closed_at ASC
            """,
            (start_ts, end_ts, start_ts, end_ts, start_iso, end_iso),
        )
        for r in cur.fetchall() or []:
            rows.append(
                TradeRow(
                    symbol=r["symbol"],
                    timeframe=r["timeframe"],
                    status=r["status"],
                    pnl=(float(r["pnl"]) if r["pnl"] is not None else None),
                    rr=(float(r["rr"]) if r["rr"] is not None else None),
                    closed_at=r["closed_at"],
                    trade_mode=r["trade_mode"] if "trade_mode" in r.keys() else None,
                    close_reason=r["close_reason"] if "close_reason" in r.keys() else None,
                )
            )

        # fallback: якщо закриття не логуються у closed_at — пробуємо opened_at
        if not rows:
            try:
                cur.execute(
                    """
                    SELECT symbol, timeframe, status, pnl, rr, opened_at as closed_at,
                           COALESCE(close_reason, reason_close) as close_reason
                    FROM trades
                    WHERE status IN ('WIN','LOSS','CLOSED','TP','SL')
                      AND opened_at IS NOT NULL
                      AND opened_at >= ? AND opened_at < ?
                    ORDER BY opened_at ASC
                    """,
                    (start_iso, end_iso),
                )
                for r in cur.fetchall() or []:
                    rows.append(
                        TradeRow(
                            symbol=r["symbol"],
                            timeframe=r["timeframe"],
                            status=r["status"],
                            pnl=(float(r["pnl"]) if r["pnl"] is not None else None),
                            rr=(float(r["rr"]) if r["rr"] is not None else None),
                            closed_at=r["closed_at"],
                            close_reason=r["close_reason"] if "close_reason" in r.keys() else None,
                        )
                    )
            except Exception as e:
                log.debug("daily_tracker fallback by opened_at failed: %s", e)
    return rows

def _fmt_f(v: Optional[float], digits: int = 2, dash: str = "0.00") -> str:
    try:
        if v is None:
            return dash
        return f"{float(v):.{digits}f}"
    except Exception:
        return dash

def _fmt_rr(v: Optional[float]) -> str:
    try:
        if v is None:
            return "-"
        return f"{float(v):.2f}"
    except Exception:
        return "-"


def _trade_outcome(t: TradeRow) -> str:
    status = (t.status or "").upper()
    reason = (t.close_reason or "").upper()
    if status == "WIN" or reason == "TP":
        return "WIN"
    if status == "LOSS" or reason in {"SL", "STOP", "REVERSED"}:
        return "LOSS"
    try:
        if t.pnl is not None and float(t.pnl) > 0:
            return "WIN"
        if t.pnl is not None and float(t.pnl) < 0:
            return "LOSS"
    except Exception:
        pass
    try:
        if t.rr is not None and float(t.rr) > 0:
            return "WIN"
        if t.rr is not None and float(t.rr) < 0:
            return "LOSS"
    except Exception:
        pass
    return status or "CLOSED"

# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------
def compute_daily_summary(*_args, **_kwargs) -> str:
    """
    Текст для /daily_now: лише закриті угоди за сьогодні (Europe/Kyiv).
    Приймає ігноровані *args/**kwargs, щоб бути толерантною до хендлерів.
    """
    date_str, start_iso, end_iso = _bounds_for_day_kyiv(datetime.now(TZ))
    trades = _fetch_trades_closed_between(start_iso, end_iso)

    total = len(trades)
    wins = sum(1 for t in trades if _trade_outcome(t) == "WIN")
    losses = sum(1 for t in trades if _trade_outcome(t) == "LOSS")
    wr = (wins / total * 100.0) if total else 0.0

    rr_vals = [t.rr for t in trades if isinstance(t.rr, (int, float))]
    pnl_vals = [t.pnl for t in trades if isinstance(t.pnl, (int, float))]
    avg_rr = (sum(rr_vals) / len(rr_vals)) if rr_vals else 0.0
    sum_pnl = sum(pnl_vals) if pnl_vals else 0.0
    avg_pnl = (sum(pnl_vals) / len(pnl_vals)) if pnl_vals else 0.0

    lines: List[str] = []
    for t in trades[:20]:
        tf = t.timeframe or "-"
        pnl_s = _fmt_f(t.pnl, 2, dash="-")
        rr_s = _fmt_rr(t.rr)
        lines.append(f"• {t.symbol} [{tf}] {_trade_outcome(t)} | PnL: {pnl_s} | RR: {rr_s}")
    details = "\n".join(lines) if lines else "—"

    text = (
        f"📆 Daily P&L — {date_str} (TZ: Europe/Kyiv)\n"
        f"Closed trades: {total} | WIN: {wins} | LOSS: {losses} | Winrate: {wr:.2f}%\n"
        f"Sum PnL$: {_fmt_f(sum_pnl, 2)} | Avg RR: {_fmt_rr(avg_rr)} | Avg PnL$: {_fmt_f(avg_pnl, 2)}\n"
        f"\n{details}"
    )
    return text

def compute_kpis(*_args, period_days: int = None, rr_bucket: float = None, **_kwargs) -> str:
    def compute_kpi(*args, **kwargs):
        return compute_kpis(*args, **kwargs)

    def get_kpi(*args, **kwargs):
        return compute_kpis(*args, **kwargs)

    def get_kpis(*args, **kwargs):
        return compute_kpis(*args, **kwargs)
    """
    
    KPI з панелі/команди /kpi.
    - період: останні N днів (за замовчуванням 7)
    - bucket RR: поріг для блоку RR≥X (за замовчуванням береться з settings.kpi_rr_bucket або 2)
    Повертає готовий текст.
    """
    # параметри з settings/env
    if period_days is None:
        try:
            period_days = int(os.getenv("KPI_DAYS", "7"))
        except Exception:
            period_days = 7
    if rr_bucket is None:
        try:
            rr_bucket = float(_get_setting("kpi_rr_bucket", os.getenv("KPI_RR_BUCKET", "2")))
        except Exception:
            rr_bucket = 2.0

    start_iso, end_iso = _bounds_for_range_days(period_days)
    trades = _fetch_trades_closed_between(start_iso, end_iso)

    total = len(trades)
    wins = sum(1 for t in trades if _trade_outcome(t) == "WIN")
    losses = sum(1 for t in trades if _trade_outcome(t) == "LOSS")
    wr = (wins / total * 100.0) if total else 0.0

    rr_vals = [t.rr for t in trades if isinstance(t.rr, (int, float))]
    pnl_vals = [t.pnl for t in trades if isinstance(t.pnl, (int, float))]
    avg_rr = (sum(rr_vals) / len(rr_vals)) if rr_vals else 0.0
    sum_pnl = sum(pnl_vals) if pnl_vals else 0.0

    # RR bucket
    rr_bucket_vals = [t for t in trades if isinstance(t.rr, (int, float)) and t.rr >= rr_bucket]
    rr_bucket_cnt = len(rr_bucket_vals)
    rr_bucket_pnl = sum((t.pnl or 0.0) for t in rr_bucket_vals)

    # Топ символів по кількості угод (до 5)
    from collections import Counter
    sym_counter = Counter([t.symbol for t in trades])
    top_syms = ", ".join(f"{s}:{n}" for s, n in sym_counter.most_common(5)) if sym_counter else "—"

    # Підрахунок відкритих ордерів
    with _conn() as c:
        cur = c.cursor()
        cur.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN'")
        open_orders = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN' AND trade_mode='scalping'")
        open_scalp = cur.fetchone()[0]

    # ═══════════════════════════════════════════════════════════════════════════
    # SCALPING KPI - окремий блок
    # ═══════════════════════════════════════════════════════════════════════════
    scalp_trades = [t for t in trades if (t.trade_mode or "").lower() == "scalping"]
    scalp_total = len(scalp_trades)
    scalp_wins = sum(1 for t in scalp_trades if _trade_outcome(t) == "WIN")
    scalp_wr = (scalp_wins / scalp_total * 100.0) if scalp_total else 0.0
    scalp_pnl_vals = [t.pnl for t in scalp_trades if isinstance(t.pnl, (int, float))]
    scalp_sum_pnl = sum(scalp_pnl_vals) if scalp_pnl_vals else 0.0
    scalp_rr_vals = [t.rr for t in scalp_trades if isinstance(t.rr, (int, float))]
    scalp_avg_rr = (sum(scalp_rr_vals) / len(scalp_rr_vals)) if scalp_rr_vals else 0.0
    
    # Standard KPI
    std_trades = [t for t in trades if (t.trade_mode or "").lower() != "scalping"]
    std_total = len(std_trades)
    std_wins = sum(1 for t in std_trades if _trade_outcome(t) == "WIN")
    std_wr = (std_wins / std_total * 100.0) if std_total else 0.0
    std_pnl_vals = [t.pnl for t in std_trades if isinstance(t.pnl, (int, float))]
    std_sum_pnl = sum(std_pnl_vals) if std_pnl_vals else 0.0

    text = (
        f"📊 KPI last {period_days}d (TZ: Europe/Kyiv)\n"
        f"═══════════════════════════\n"
        f"📈 ALL: {total} | WR: {wr:.1f}% | PNL$: {_fmt_f(sum_pnl, 2)}\n"
        f"Open: {open_orders} (scalp: {open_scalp})\n"
        f"RR≥{rr_bucket:g}: {rr_bucket_cnt} | PNL$: {_fmt_f(rr_bucket_pnl, 2)}\n"
        f"───────────────────────────\n"
        f"⚡ SCALPING: {scalp_total} | WR: {scalp_wr:.1f}% | PNL$: {_fmt_f(scalp_sum_pnl, 2)} | RR: {_fmt_rr(scalp_avg_rr)}\n"
        f"📉 STANDARD: {std_total} | WR: {std_wr:.1f}% | PNL$: {_fmt_f(std_sum_pnl, 2)}\n"
        f"───────────────────────────\n"
        f"Top: {top_syms}"
    )
    return text

async def daily_tracker_job(bot) -> None:
    """
    Джоб для щоденної розсилки в 23:59 (налаштовано в main.py).
    Відправляє summary тим, у кого daily_tracker=1.
    """
    try:
        text = compute_daily_summary()
    except Exception as e:
        log.warning("daily_tracker: compute failed: %s", e)
        text = f"⚠️ daily summary failed: {e}"

    # розсилка
    try:
        with _conn() as c:
            cur = c.cursor()
            cur.execute("SELECT user_id FROM user_settings WHERE COALESCE(daily_tracker,0)=1")
            user_ids = [int(r[0]) for r in cur.fetchall() or []]
    except Exception as e:
        log.warning("daily_tracker: fetch users failed: %s", e)
        user_ids = []

    for uid in user_ids:
        try:
            await bot.send_message(chat_id=uid, text=text, disable_web_page_preview=True)
        except Exception as e:
            log.warning("daily_tracker: send to %s failed: %s", uid, e)
