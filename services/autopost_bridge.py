from __future__ import annotations
import re
import json
import logging
import os
import sqlite3
import math
from typing import Optional, Dict, List

from core_config import CFG

log = logging.getLogger("autopost_bridge")
DB_PATH = (
    os.getenv("DB_PATH")
    or os.getenv("SQLITE_PATH")
    or os.getenv("DATABASE_PATH")
    or CFG.get("db_path", "storage/bot.db")
)

# Якщо маєш локальні свічки — використаємо для закриття REVERSED по ринку
try:
    from market_data.candles import get_ohlcv  # get_ohlcv(symbol, timeframe, n) -> list[{"close": ...}]
except Exception:
    get_ohlcv = None

# ──────────────────────────────────────────────────────────────────────────────
# Parsers for autopost text
# Examples supported:
# 🤖 Autopost plan ETHUSDT [1h]
# Dir: LONG | RR≈2.4211
# Direction: SHORT  RR=1.8
# 🔴 SHORT | RR:1.95
# Entry: 4588.0000 | SL: 4550.0000 | TP: 4680.0000
# ──────────────────────────────────────────────────────────────────────────────
_RE_HEADER = re.compile(r"Autopost plan\s+([A-Z0-9]+)\s+\[([^\]]+)\]", re.I)
# Primary pattern: Dir: LONG | RR≈2.4 (backwards compatible)
_RE_DIR_RR = re.compile(r"Dir:\s*(LONG|SHORT)\s*\|\s*RR[≈~=]?\s*([0-9.]+)", re.I)
# Fallback: separate DIR or RR tokens (supports BUY/SELL and some emojis)
_RE_DIR_TOKEN = re.compile(r"\b(LONG|SHORT|BUY|SELL)\b|[\U0001F7E2\U0001F534\u2B06\uFE0F\u2B07\uFE0F]", re.I)
_RE_RR_TOKEN = re.compile(r"RR[≈~=]?[\s:=]*([0-9]+(?:\.[0-9]+)?)", re.I)
_RE_LVLS   = re.compile(r"Entry:\s*([0-9\s,\.]+)\s*\|\s*SL:\s*([0-9\s,\.]+)\s*\|\s*TP:\s*([0-9\s,\.]+)", re.I)
# Mode indicator — look for "скальп" / "scalp" or "класичн" / "standard" in the message
_RE_MODE = re.compile(r"(скальп|scalp|scalping|класичн|classic|standard)", re.I)

def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

def _parse(text: str) -> Optional[Dict]:
    """Парсимо текст повідомлення і повертаємо план або None.
    Підтримуємо як класичний `Dir: LONG | RR≈2.4`, так і варіанти з окремими токенами
    (наприклад `SHORT RR=1.8` або емоджі 🔴/🟢).
    """
    body = text or ""
    m1 = _RE_HEADER.search(body)
    m3 = _RE_LVLS.search(body)
    if not (m1 and m3):
        return None

    # Try combined Dir+RR first (backwards compatible)
    m2 = _RE_DIR_RR.search(body)
    direction = None
    rr = None
    if m2:
        direction = m2.group(1).upper()
        try:
            rr = float(m2.group(2))
        except Exception:
            rr = None
    else:
        # Fallback: find direction token
        m_dir = _RE_DIR_TOKEN.search(body)
        if m_dir:
            token = m_dir.group(0)
            # Normalize common emoji and arrow tokens
            if token in ("🟢", "🔴", "⬆️", "⬇️", "\u2B06\uFE0F", "\u2B07\uFE0F"):
                if token in ("🟢", "⬆️", "\u2B06\uFE0F"):
                    direction = "LONG"
                else:
                    direction = "SHORT"
            else:
                tok = token.upper()
                if tok in ("LONG", "BUY"):
                    direction = "LONG"
                elif tok in ("SHORT", "SELL"):
                    direction = "SHORT"
        # Fallback RR token
        m_rr = _RE_RR_TOKEN.search(body)
        if m_rr:
            try:
                rr = float(m_rr.group(1))
            except Exception:
                rr = None

    if not direction or rr is None:
        # invalid/unsupported format
        return None

    symbol, timeframe = m1.group(1).upper(), m1.group(2)
    def _num_to_float(s: str) -> float:
        try:
            # remove thousands separators (space, comma) that are used for prettier formatting
            return float(str(s).replace(' ', '').replace(',', ''))
        except Exception:
            return float(s)

    # Normalize numbers with spaces/commas
    def _clean_num(s: str) -> float:
        return float(s.replace(' ', '').replace(',', ''))

    entry, sl, tp = _clean_num(m3.group(1)), _clean_num(m3.group(2)), _clean_num(m3.group(3))

    # Try detect trade_mode from body (scalping / standard)
    trade_mode = None
    m_mode = _RE_MODE.search(body)
    if m_mode:
        tok = m_mode.group(1).lower()
        if 'скальп' in tok or 'scalp' in tok or 'scalping' in tok:
            trade_mode = 'scalping'
        else:
            trade_mode = 'standard'

    res = {
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,   # 'LONG' або 'SHORT'
        "rr": rr,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "source": "autopost",
    }
    if trade_mode:
        res['trade_mode'] = trade_mode
    return res

def _last_price(symbol: str, timeframe: str) -> Optional[float]:
    if get_ohlcv is None:
        return None
    try:
        bars = get_ohlcv(symbol, timeframe, 2)
        if not bars:
            return None
        return float(bars[-1]["close"])
    except Exception:
        return None

def _pnl_rr(direction: str, entry: float, sl: float, close_price: float, size_usd: float, fees_bps: int) -> tuple[float, float]:
    denom = abs(entry - sl) if sl is not None else 0.0
    rr_real = (abs(close_price - entry) / denom) if denom > 0 else 0.0
    if direction == "LONG":
        pnl_usd = size_usd * ((close_price - entry) / entry)
    else:
        pnl_usd = size_usd * ((entry - close_price) / entry)
    pnl_usd -= size_usd * (fees_bps / 10000.0) * 2.0
    return float(pnl_usd), float(rr_real)

def _has_open_trade(cur: sqlite3.Cursor, symbol: str, timeframe: str, trade_mode: str | None = None) -> bool:
    if trade_mode:
        row = cur.execute(
            "SELECT 1 FROM trades WHERE symbol=? AND timeframe=? AND status='OPEN' AND trade_mode=? LIMIT 1",
            (symbol, timeframe, trade_mode),
        ).fetchone()
    else:
        row = cur.execute(
            "SELECT 1 FROM trades WHERE symbol=? AND timeframe=? AND status='OPEN' LIMIT 1",
            (symbol, timeframe),
        ).fetchone()
    return bool(row)

def _find_open_trades(cur: sqlite3.Cursor, symbol: str, timeframe: str, trade_mode: str | None = None) -> List[sqlite3.Row]:
    if trade_mode:
        return cur.execute(
            "SELECT * FROM trades WHERE symbol=? AND timeframe=? AND status='OPEN' AND trade_mode=? ORDER BY id",
            (symbol, timeframe, trade_mode),
        ).fetchall()
    return cur.execute(
        "SELECT * FROM trades WHERE symbol=? AND timeframe=? AND status='OPEN' ORDER BY id",
        (symbol, timeframe),
    ).fetchall()

def _close_trade(cur: sqlite3.Cursor, trade: sqlite3.Row, reason: str, at_price: Optional[float]):
    price = float(at_price if at_price is not None else trade["entry"])
    pnl_usd, rr_real = _pnl_rr(
        direction=trade["direction"].upper(),
        entry=float(trade["entry"]),
        sl=float(trade["sl"]),
        close_price=price,
        size_usd=float(trade["size_usd"] or 100.0),
        fees_bps=int(trade["fees_bps"] or 10),
    )
    trade_id = int(trade["id"])
    cur.execute(
        """
        UPDATE trades
           SET status='CLOSED',
               closed_at=datetime('now'),
               close_reason=?,
               close_price=?,
               pnl_usd=COALESCE(pnl_usd,0)+?,
               rr_realized=?
         WHERE id=?
        """,
        (reason, price, pnl_usd, rr_real, trade_id),
    )

    if trade["signal_id"]:
        cur.execute("UPDATE signals SET status='CLOSED', closed_at=datetime('now') WHERE id=? AND status='OPEN'",
                    (int(trade["signal_id"]),))

def _insert_signal(cur: sqlite3.Cursor, plan: Dict, user_id: int) -> int:
    details = {
        "trade_mode": plan.get("trade_mode"),
        "reasons": plan.get("reasons"),
        "hard_blockers": plan.get("hard_blockers"),
        "gate": {
            "score": plan.get("gate_score"),
            "total": plan.get("gate_total"),
            "pct": plan.get("gate_pct"),
            "details": plan.get("gate_details"),
        },
        "ind": plan.get("ind"),
    }
    cur.execute(
        """
        INSERT INTO signals(user_id, symbol, timeframe, direction, entry, sl, tp, rr, source, status, opened_at, details)
        VALUES(?,?,?,?,?,?,?,?,?,'OPEN',datetime('now'),?)
        """,
        (
            int(user_id),
            plan["symbol"], plan["timeframe"], plan["direction"],
            float(plan["entry"]), float(plan["sl"]), float(plan["tp"]),
            float(plan["rr"]),
            plan.get("source","autopost"),
            json.dumps(details, ensure_ascii=False),
        ),
    )
    return int(cur.lastrowid)

def _insert_trade(cur: sqlite3.Cursor, plan: Dict, signal_id: Optional[int]) -> int:
    size_usd = float(CFG.get("sim_usd_per_trade", 100))
    fees_bps = int(CFG.get("fees_bps", 10))
    rr_planned = float(plan.get("rr") or 0.0)
    # Allow plan to specify trade_mode (scalping/standard/ai). Fallback to 'standard'.
    trade_mode = str(plan.get("trade_mode") or plan.get("mode") or "standard")
    indicators_json = None
    try:
        indicators_json = json.dumps(
            {
                "ind": plan.get("ind"),
                "reasons": plan.get("reasons"),
                "hard_blockers": plan.get("hard_blockers"),
                "gate": {
                    "score": plan.get("gate_score"),
                    "total": plan.get("gate_total"),
                    "pct": plan.get("gate_pct"),
                    "details": plan.get("gate_details"),
                },
            },
            ensure_ascii=False,
        )
    except Exception:
        indicators_json = None

    for col, typ in [
        ("indicators_json", "TEXT"),
        ("gate_score", "INTEGER"),
        ("gate_total", "INTEGER"),
        ("gate_pct", "REAL"),
        ("slippage_pct", "REAL"),
        ("rr_raw", "REAL"),
        ("rr_adj", "REAL"),
    ]:
        try:
            cur.execute(f"ALTER TABLE trades ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass
    cur.execute(
        """
        INSERT INTO trades(
            signal_id, symbol, timeframe, direction, entry, sl, tp,
            opened_at, status, close_reason, size_usd, fees_bps, rr_planned, trade_mode,
            indicators_json, gate_score, gate_total, gate_pct, slippage_pct, rr_raw, rr_adj
        )
        VALUES(?,?,?,?,?,?,?,datetime('now'),'OPEN',NULL,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            signal_id,
            plan["symbol"], plan["timeframe"], plan["direction"],
            float(plan["entry"]), float(plan["sl"]), float(plan["tp"]),
            size_usd, fees_bps, rr_planned, trade_mode,
            indicators_json, plan.get("gate_score"), plan.get("gate_total"), plan.get("gate_pct"),
            plan.get("slippage_pct"), plan.get("rr_raw"), plan.get("rr_adj"),
        ),
    )
    return int(cur.lastrowid)

def handle_autopost_message(msg: Dict) -> Optional[int]:
    """
    Приймає дикт з автопоста (msg['text'], msg.get('meta', {})):
      - парсить план;
      - перевіряє RR-поріг (user_rr у meta або CFG.autopost_rr_min, деф.1.5);
      - якщо вже є OPEN з тим самим напрямом — скіпає;
      - якщо вже є OPEN з протилежним напрямом — СПОЧАТКУ закриває їх (REVERSED) за останньою ціною (або entry), потім відкриває нову;
      - створює рядки у signals та trades; повертає trade_id.
    """
    text = (msg or {}).get("text") or ""
    plan = _parse(text)
    if not plan:
        return None

    meta = msg.get("meta") if isinstance(msg.get("meta"), dict) else {}
    user_rr = None
    try:
        if "user_rr" in meta:
            user_rr = float(meta["user_rr"])
    except Exception:
        user_rr = None
    rr_thr = user_rr
    if rr_thr is None:
        try:
            rr_thr = float(CFG.get("autopost_rr_min", 1.5))
        except Exception:
            rr_thr = 1.5

    if float(plan["rr"]) < rr_thr:
        log.info("autopost_bridge: skip %s [%s]: rr=%.2f < thr=%.2f",
                 plan["symbol"], plan["timeframe"], plan["rr"], rr_thr)
        return None

    user_id = 0
    try:
        user_id = int(meta.get("user_id", 0))
    except Exception:
        user_id = 0

    with _conn() as c:
        cur = c.cursor()

        # Ensure unique indices to prevent duplicate OPEN trades.
        # We prefer a trade_mode-aware index so autopost and ai trades can coexist (same symbol/timeframe but different trade_mode).
        # Try to DROP old index (if present) that enforced uniqueness across all trade_modes.
        try:
            cur.execute("DROP INDEX IF EXISTS uniq_trades_open")
        except Exception:
            pass
        try:
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS uniq_trades_open_mode ON trades(symbol, timeframe, trade_mode) WHERE status='OPEN'")
        except Exception:
            # older SQLite versions might not support partial index with extra column — ignore
            pass

        # Acquire an immediate lock to avoid race conditions with concurrent autposts
        try:
            cur.execute("BEGIN IMMEDIATE")
        except Exception:
            # If can't acquire immediately, proceed — sqlite will serialize operations via timeout
            pass

        # Якщо є відкриті — перевіряємо напрям
        plan_mode = plan.get('trade_mode')
        open_rows = _find_open_trades(cur, plan["symbol"], plan["timeframe"], trade_mode=plan_mode)
        if open_rows:
            same_dir = [r for r in open_rows if (r["direction"] or "").upper() == plan["direction"].upper()]
            opp_dir  = [r for r in open_rows if (r["direction"] or "").upper() != plan["direction"].upper()]

            if same_dir and not opp_dir:
                log.info("autopost_bridge: already OPEN same dir %s [%s], skip",
                         plan["symbol"], plan["timeframe"])
                return None

            # Закриваємо протилежні перед відкриттям нової
            if opp_dir:
                last = _last_price(plan["symbol"], plan["timeframe"])
                for tr in opp_dir:
                    _close_trade(cur, tr, reason="REVERSED", at_price=last)

        # Ensure plan includes trade_mode when called from autopost
        # Determine plan_trade_mode from various places: explicit message field, meta, or parsed plan
        plan_trade_mode = None
        if plan and isinstance(plan, dict):
            plan_trade_mode = plan.get("trade_mode") or plan_trade_mode
        if isinstance(msg, dict):
            plan_trade_mode = msg.get("trade_mode") or (msg.get("meta") or {}).get("trade_mode") or plan_trade_mode
        if plan_trade_mode:
            plan["trade_mode"] = plan_trade_mode
        for extra_key in (
            "ind", "reasons", "hard_blockers", "gate_details",
            "gate_score", "gate_total", "gate_pct",
            "slippage_pct", "rr_raw", "rr_adj", "rr_target",
        ):
            if isinstance(msg, dict) and extra_key in msg:
                plan[extra_key] = msg.get(extra_key)

        # Тепер створюємо сигнал і трейд
        sig_id = _insert_signal(cur, plan, user_id=user_id)
        trade_id = _insert_trade(cur, plan, signal_id=sig_id)
        c.commit()

    log.info("autopost_bridge: OPEN trade#%s %s [%s] %s @%.4f SL=%.4f TP=%.4f (RR≈%.2f) sig#%s",
             trade_id, plan["symbol"], plan["timeframe"], plan["direction"],
             plan["entry"], plan["sl"], plan["tp"], plan["rr"], sig_id)
    return trade_id
