import os
import sqlite3
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple, Any
from contextlib import contextmanager
import logging

# Use centralized DB connection
try:
    from utils.db import get_conn as _get_db_conn
except ImportError:
    _get_db_conn = None

DB_PATH = os.getenv("DB_PATH", "storage/bot.db")

# Додаємо логування для трейдів
logger = logging.getLogger("trade_engine")

@contextmanager
def _connect():
    """Create database connection using centralized path."""
    if _get_db_conn:
        with _get_db_conn() as conn:
            yield conn
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

def _now_iso() -> str:
    """Get current UTC time as ISO string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def get_setting(key: str, default: str) -> str:
    """Get setting value from database."""
    with _connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return (row["value"] if row else default)

def set_setting(key: str, value: str) -> None:
    """Set setting value in database."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value;",
            (key, value),
        )

def _get_open_trade(conn: sqlite3.Connection, symbol: str, timeframe: str) -> Optional[sqlite3.Row]:
    """Get open trade for symbol and timeframe."""
    return conn.execute(
        "SELECT * FROM trades WHERE symbol=? AND timeframe=? AND status='OPEN' "
        "ORDER BY opened_at DESC LIMIT 1",
        (symbol, timeframe),
    ).fetchone()

def get_open_trade(symbol: str, timeframe: str) -> Optional[dict]:
    """Public helper: return OPEN trade as dict or None."""
    with _connect() as conn:
        row = _get_open_trade(conn, symbol, timeframe)
        if not row:
            return None
        try:
            return dict(row)
        except Exception:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()]
            return {c: row[idx] for idx, c in enumerate(cols)}


def _rr(entry: float, sl: float, tp: float) -> Optional[float]:
    """Calculate risk/reward ratio."""
    try:
        risk = abs(entry - sl)
        reward = abs(tp - entry)
        if risk <= 0:
            return None
        return reward / risk
    except Exception:
        return None

def _round(x: float) -> float:
    """Round float to 6 decimal places."""
    return float(f"{x:.6f}")

def open_trade_from_signal(signal: Dict[str, Any], trade_mode: str = "standard") -> Optional[int]:
    """
    Idempotent open - only if no OPEN trade exists for (symbol,timeframe).
    Expected keys in `signal` (best effort):
      - id (signal_id) | symbol | timeframe | direction ('LONG'/'SHORT')
      - entry | sl | tp | rr (optional)
      - ind (dict with all indicators - will be saved as JSON)
      - gate_score, gate_total, gate_pct (gate results)
      - slippage_pct, rr_raw, rr_adj (scalping params)
    
    Args:
        signal: Dict with trade parameters
        trade_mode: 'standard' or 'scalping'
    """
    import json
    
    symbol = signal.get("symbol")
    timeframe = signal.get("timeframe") or signal.get("tf") or "1h"
    direction = (signal.get("direction") or "").upper()
    if direction not in ("LONG", "SHORT"):
        return None

    entry = float(signal.get("entry"))
    sl = float(signal.get("sl"))
    tp = float(signal.get("tp"))
    rr_planned = signal.get("rr")
    if rr_planned is None:
        rr_planned = _rr(entry, sl, tp)

    signal_id = signal.get("id")
    size_usd = float(get_setting("sim_usd_per_trade", "100"))
    fees_bps = int(get_setting("fees_bps", "10"))

    # Додаємо логування для сигналу
    logger.info(f"Обробка сигналу: {signal}")

    # ══════════════════════════════════════════════════════════════════════════
    # SCALPING / INDICATOR FIELDS
    # ══════════════════════════════════════════════════════════════════════════
    ind = signal.get("ind", {})
    # Конвертуємо ind в JSON (без gate_details, бо там може бути занадто багато)
    indicators_json = None
    if ind or signal.get("reasons") or signal.get("hard_blockers"):
        meta_payload = {
            "ind": ind,
            "reasons": signal.get("reasons"),
            "hard_blockers": signal.get("hard_blockers"),
            "gate_details": signal.get("gate_details") or (ind.get("gate_details") if isinstance(ind, dict) else None),
        }
        try:
            indicators_json = json.dumps(meta_payload, ensure_ascii=False)
        except Exception:
            indicators_json = None
    
    gate_score = signal.get("gate_score")
    gate_total = signal.get("gate_total")
    gate_pct = signal.get("gate_pct")
    slippage_pct = signal.get("slippage_pct")
    rr_raw = signal.get("rr_raw")
    rr_adj = signal.get("rr_adj")
    
    # Get EMA values from ind if available
    ema50 = ind.get("ema50") if ind else None
    ema200 = ind.get("ema200") if ind else None
    atr_entry = ind.get("atr14") or ind.get("atr") if ind else None
    rr_target = signal.get("rr_target") or rr_adj

    with _connect() as conn:
        signal_cols = [r[1] for r in conn.execute("PRAGMA table_info(signals)").fetchall()]
        # Ensure new columns exist
        for col, typ in [
            ("trade_mode", "TEXT"),
            ("indicators_json", "TEXT"),
            ("gate_score", "INTEGER"),
            ("gate_total", "INTEGER"),
            ("gate_pct", "REAL"),
            ("slippage_pct", "REAL"),
            ("rr_raw", "REAL"),
            ("rr_adj", "REAL"),
        ]:
            try:
                conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass  # Column already exists

        # Ensure trade_mode-aware unique index exists (drop legacy index that prevented multiple trade_mode OPEN trades)
        try:
            conn.execute("DROP INDEX IF EXISTS uniq_trades_open")
        except Exception:
            pass
        try:
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uniq_trades_open_mode ON trades(symbol, timeframe, trade_mode) WHERE status='OPEN'")
        except Exception:
            pass
        
        # Ensure we do not open duplicate trades of the same trade_mode (allow _different_ trade_modes to coexist)
        try:
            existing_same_mode = conn.execute(
                "SELECT * FROM trades WHERE symbol=? AND timeframe=? AND status='OPEN' AND trade_mode=? "
                "ORDER BY opened_at DESC LIMIT 1",
                (symbol, timeframe, trade_mode),
            ).fetchone()
        except Exception:
            # Fallback to conservative behaviour if column/index missing
            existing_same_mode = _get_open_trade(conn, symbol, timeframe)

        if existing_same_mode:
            existing_id = None
            try:
                existing_id = existing_same_mode['id']
            except Exception:
                try:
                    existing_id = existing_same_mode[0]
                except Exception:
                    existing_id = None
            logger.info(f"Трейд для {symbol} на таймфреймі {timeframe} з trade_mode={trade_mode} вже відкритий (id={existing_id}).")
            return None

        cur = conn.execute(
            """INSERT INTO trades(
                signal_id, symbol, timeframe, direction, entry, sl, tp, opened_at,
                size_usd, fees_bps, rr_planned, status, trade_mode,
                indicators_json, gate_score, gate_total, gate_pct,
                slippage_pct, rr_raw, rr_adj, ema50, ema200, atr_entry, rr_target
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                signal_id, symbol, timeframe, direction,
                _round(entry), _round(sl), _round(tp),
                _now_iso(), size_usd, fees_bps, rr_planned, "OPEN", trade_mode,
                indicators_json, gate_score, gate_total, gate_pct,
                slippage_pct, rr_raw, rr_adj, ema50, ema200, atr_entry, rr_target,
            ),
        )
        logger.info(f"Трейд відкрито: ID={cur.lastrowid}, символ={symbol}, напрямок={direction}, RR={rr_planned}")
        if signal_id and "details" in signal_cols:
            try:
                conn.execute(
                    "UPDATE signals SET details=?, trade_id=? WHERE id=?",
                    (
                        json.dumps(
                            {
                                "trade_mode": trade_mode,
                                "reasons": signal.get("reasons"),
                                "hard_blockers": signal.get("hard_blockers"),
                                "gate_score": gate_score,
                                "gate_total": gate_total,
                                "gate_pct": gate_pct,
                                "ind": ind,
                            },
                            ensure_ascii=False,
                        ),
                        int(cur.lastrowid),
                        int(signal_id),
                    ),
                )
            except Exception:
                logger.debug("Не вдалося оновити details для signal_id=%s", signal_id, exc_info=True)
        return cur.lastrowid

def _close_pnl(direction: str, entry: float, close: float, size_usd: float, fees_bps: int) -> Tuple[float, float]:
    """Calculate P&L for closed trade."""
    qty = size_usd / entry  # simulated quantity
    gross = (close - entry) * qty if direction == "LONG" else (entry - close) * qty
    # round-trip fees on notional (open+close): approx 2 legs * bps
    notional = qty * entry + qty * close
    fees = (fees_bps / 10000.0) * notional
    pnl_usd = gross - fees
    pnl_pct = (pnl_usd / size_usd) * 100.0
    return (_round(pnl_usd), _round(pnl_pct))

def close_trade(symbol: str, timeframe: str, price: float, reason: str, win_loss_hint: Optional[str] = None) -> Optional[int]:
    with _connect() as conn:
        tr = _get_open_trade(conn, symbol, timeframe)
        if not tr:
            return None
        # Support both sqlite3.Row/dict-like and tuple rows (older connections)
        def _get(fld: str):
            try:
                return tr[fld]
            except Exception:
                # tuple fallback: build mapping from PRAGMA table_info
                cols = [r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()]
                try:
                    idx = cols.index(fld)
                    return tr[idx]
                except Exception:
                    return None

        pnl_usd, pnl_pct = _close_pnl(_get("direction"), float(_get("entry")), float(price), float(_get("size_usd")), int(_get("fees_bps") or 0))
        rr_realized = None
        sl_val = _get("sl")
        entry_val = _get("entry")
        if sl_val is not None and float(sl_val) != float(entry_val):
            rr_realized = abs(float(price) - float(entry_val)) / abs(float(entry_val) - float(sl_val))
        status = win_loss_hint if win_loss_hint in ("WIN", "LOSS") else ("WIN" if pnl_usd > 0 else "LOSS")
        tr_id = _get("id")
        # Write only columns that exist in the DB (backwards compatibility)
        cols_present = [r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()]
        updates = []
        params = []
        def add(col, val):
            updates.append(f"{col}=?")
            params.append(val)
        add("closed_at", _now_iso())
        if "close_price" in cols_present:
            add("close_price", _round(price))
        if "close_reason" in cols_present:
            add("close_reason", reason)
        if "pnl_usd" in cols_present:
            add("pnl_usd", pnl_usd)
        if "pnl_pct" in cols_present:
            add("pnl_pct", pnl_pct)
        if "rr_realized" in cols_present:
            add("rr_realized", rr_realized)
        if "status" in cols_present:
            add("status", status)
        params.append(tr_id)
        sql = f"UPDATE trades SET {', '.join(updates)} WHERE id=?"
        conn.execute(sql, tuple(params))
        return tr_id

def evaluate_open_trades(price_map: Optional[Dict[Tuple[str, str], float]] = None) -> int:
    """
    Evaluates TP/SL hits for OPEN trades using provided price_map:
        { (symbol, timeframe): last_price }
    Returns number of closed trades.
    If no price_map supplied, it's a no-op (safe for schedulers).
    """
    if not price_map:
        return 0
    closed = 0
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall()
        for tr in rows:
            key = (tr["symbol"], tr["timeframe"])
            price = price_map.get(key)
            if price is None:
                continue
            entry, sl, tp = float(tr["entry"]), float(tr["sl"]), float(tr["tp"])
            if tr["direction"] == "LONG":
                if price >= tp:
                    close_trade(tr["symbol"], tr["timeframe"], price, "TP", win_loss_hint="WIN")
                    closed += 1
                elif price <= sl:
                    close_trade(tr["symbol"], tr["timeframe"], price, "SL", win_loss_hint="LOSS")
                    closed += 1
            else:  # SHORT
                if price <= tp:
                    close_trade(tr["symbol"], tr["timeframe"], price, "TP", win_loss_hint="WIN")
                    closed += 1
                elif price >= sl:
                    close_trade(tr["symbol"], tr["timeframe"], price, "SL", win_loss_hint="LOSS")
                    closed += 1
    return closed

def handle_neutral_transition(symbol: str, timeframe: str, price: float, atr: Optional[float], mode: Optional[str] = None) -> Optional[str]:
    """
    Applies Neutral policy to OPEN trade if present.
    mode: CLOSE | TRAIL | IGNORE (defaults to settings.neutral_mode)
    Returns action string or None.
    """
    mode = (mode or get_setting("neutral_mode", "TRAIL")).upper()
    with _connect() as conn:
        tr = _get_open_trade(conn, symbol, timeframe)
        if not tr:
            return None
        if mode == "IGNORE":
            return "IGNORED"
        if mode == "CLOSE":
            close_trade(symbol, timeframe, price, "NEUTRAL_CLOSE")
            return "CLOSED"
        # TRAIL mode
        entry = float(tr["entry"])
        sl = float(tr["sl"])
        # Fallback ATR if missing
        if atr is None or atr <= 0:
            atr = 0.005 * price  # 0.5% fallback band
        if tr["direction"] == "LONG":
            new_sl = max(sl, max(entry, price - 0.5 * atr))
            if new_sl > sl:
                conn.execute("UPDATE trades SET sl=? WHERE id=?", (_round(new_sl), tr["id"]))
                return f"TRAIL_SL→{_round(new_sl)}"
        else:
            new_sl = min(sl, min(entry, price + 0.5 * atr))
            if new_sl < sl:
                conn.execute("UPDATE trades SET sl=? WHERE id=?", (_round(new_sl), tr["id"]))
                return f"TRAIL_SL→{_round(new_sl)}"
        return "TRAIL_NOCHANGE"
