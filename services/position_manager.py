from __future__ import annotations

import logging
from typing import Optional
from datetime import datetime, timezone

from utils.db import get_conn
from utils.settings import get_setting
from services.pnl import calc_pnl_usd  # ← Додаємо імпорт
from services.decision_log import log_decision

__all__ = ["manage_open_positions"]

log = logging.getLogger("position_manager")


def _get_setting_float(key: str, default: float) -> float:
    try:
        val = get_setting(key, str(default))
        return float(val if val is not None else default)
    except Exception:
        return default


def _get_setting_bool(key: str, default: bool) -> bool:
    raw = get_setting(key, "true" if default else "false")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _get_price(sym: str) -> Optional[float]:
    for path in (
            "services.market",
            "services.price_provider",
            "services.binance_price",
            "services.binance",
            "services.prices",
    ):
        try:
            mod = __import__(path, fromlist=["get_price"])
            if hasattr(mod, "get_price"):
                return float(mod.get_price(sym))  # type: ignore[attr-defined]
        except Exception:
            continue
    return None


def _rr_eps() -> float:
    try:
        return float(get_setting("rr_eps", "1e-6") or 1e-6)
    except Exception:
        return 1e-6


def _rr_current(entry: float, sl: float, px: float, direction: str) -> float:
    r = abs(entry - sl)
    if r <= _rr_eps():
        return 0.0
    if (direction or "LONG").upper() == "LONG":
        return (px - entry) / r
    else:
        return (entry - px) / r


def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _get_trade_size_usd(conn, trade_id: int) -> float:
    """Отримує розмір позиції з БД або використовує дефолт."""
    try:
        row = conn.execute("SELECT size_usd FROM trades WHERE id=?", (trade_id,)).fetchone()
        if row and row[0] is not None:
            return float(row[0])
    except Exception:
        pass
    return float(get_setting("default_position_size_usd", "100.0") or 100.0)


def _get_fees_bps() -> int:
    """Отримує комісію в базисних пунктах з налаштувань."""
    try:
        return int(get_setting("trading_fees_bps", "10") or 10)
    except Exception:
        return 10


def _update_signal_linked(conn, trade_id: int, reason: str, closed_at: int) -> None:
    """Оновлює пов'язані сигнали при закритті позиції."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(signals)").fetchall()]
    if "trade_id" not in cols:
        return
    conn.execute(
        "UPDATE signals SET reason_close=?, closed_at=?, status='CLOSED' WHERE trade_id=?",
        (reason, closed_at, trade_id),
    )


def _close_position_with_pnl(conn, tid: int, symbol: str, direction: str,
                             entry: float, sl: float, close_price: float,
                             reason: str, partial_pct: float = 1.0) -> None:
    """Закриває позицію з детальним розрахунком PnL."""
    size_usd = _get_trade_size_usd(conn, tid)
    fees_bps = _get_fees_bps()
    close_pct = max(0.0, min(1.0, float(partial_pct or 1.0)))
    close_size_usd = size_usd * close_pct

    rr_realized, pnl_usd = calc_pnl_usd(
        entry=entry,
        sl=sl,
        close_price=close_price,
        direction=direction,
        size_usd=size_usd,
        fees_bps=fees_bps,
        partial_pct=close_pct
    )
    pnl_usd = float(pnl_usd or 0.0)

    # Старий RR для сумісності
    r = abs(entry - sl)
    if r > _rr_eps():
        if (direction or "LONG").upper() == "LONG":
            rr_old = (close_price - entry) / r
        else:
            rr_old = (entry - close_price) / r
    else:
        rr_old = 0.0

    ts = _now_ts()

    if close_pct >= 1.0:
        # Повне закриття
        conn.execute(
            """
            UPDATE trades
               SET status='CLOSED',
                   closed_at=?,
                   close_price=?,
                   close_reason=?,
                   rr_realized=?,
                   pnl_usd=COALESCE(pnl_usd,0)+?
             WHERE id=?
            """,
            (ts, close_price, reason, rr_realized, pnl_usd, tid),
        )

        _update_signal_linked(conn, tid, reason, ts)
        log.info(
            "[pm] CLOSE FULL trade#%s %s rr=%.2f→%.2f pnl=$%.2f reason=%s",
            tid, symbol, rr_old, rr_realized, pnl_usd, reason
        )
    else:
        remaining_size_usd = max(0.0, size_usd - close_size_usd)
        conn.execute(
            """
            UPDATE trades
               SET partial_50_done=1,
                   size_usd=?,
                   pnl_usd=COALESCE(pnl_usd,0)+?,
                   close_price=?,
                   rr_realized=?
             WHERE id=?
            """,
            (remaining_size_usd, pnl_usd, close_price, rr_realized, tid),
        )
        log.info(
            "[pm] PARTIAL CLOSE %.0f%% trade#%s %s pnl=$%.2f size %.2f→%.2f",
            close_pct * 100, tid, symbol, pnl_usd, size_usd, remaining_size_usd
        )


def _be_stop(direction: str, old_sl: float, entry: float) -> float:
    if (direction or "LONG").upper() == "LONG":
        return max(old_sl, entry)
    return min(old_sl, entry)


def _apply_move_be(conn, tid: int, symbol: str, direction: str, old_sl: float, entry: float) -> bool:
    new_sl = _be_stop(direction, old_sl, entry)
    if abs(new_sl - old_sl) <= 1e-12:
        conn.execute("UPDATE trades SET be_done=1 WHERE id=?", (tid,))
        moved = False
    else:
        conn.execute("UPDATE trades SET sl=?, be_done=1 WHERE id=?", (new_sl, tid,))
        moved = True
    log_decision(
        source="position_manager",
        decision="BE_MOVED" if moved else "BE_MARKED",
        reason=f"sl {old_sl:.8f}->{new_sl:.8f}",
        symbol=symbol,
        direction=direction,
        rr=None,
        risk_state="BREAK_EVEN",
        conn=conn,
    )
    return moved


def _profit_lock_stop(direction: str, entry: float, initial_sl: float, lock_r: float) -> float:
    risk = abs(entry - initial_sl)
    if risk <= _rr_eps():
        return entry
    if (direction or "LONG").upper() == "LONG":
        return entry + lock_r * risk
    return entry - lock_r * risk


def _apply_profit_lock(
    conn,
    tid: int,
    symbol: str,
    direction: str,
    old_sl: float,
    entry: float,
    initial_sl: float,
    lock_r: float,
) -> bool:
    target_sl = _profit_lock_stop(direction, entry, initial_sl, lock_r)
    if (direction or "LONG").upper() == "LONG":
        new_sl = max(old_sl, target_sl)
    else:
        new_sl = min(old_sl, target_sl)

    if abs(new_sl - old_sl) <= 1e-12:
        return False

    conn.execute("UPDATE trades SET sl=? WHERE id=?", (new_sl, tid))
    log_decision(
        source="position_manager",
        decision="PROFIT_LOCK",
        reason=f"sl {old_sl:.8f}->{new_sl:.8f}; lock_r={lock_r:.2f}",
        symbol=symbol,
        direction=direction,
        risk_state="PROFIT_LOCK",
        conn=conn,
    )
    log.info("[pm] PROFIT LOCK trade#%s %s sl: %.6f -> %.6f", tid, symbol, old_sl, new_sl)
    return True


def _apply_trail(conn, tid: int, symbol: str, direction: str, old_sl: float, px: float, atr: Optional[float]) -> float:
    k = _get_setting_float("atr_sl_mult", 2.0)
    if atr and atr > 0:
        if (direction or "LONG").upper() == "LONG":
            new_sl = max(old_sl, px - k * atr)
        else:
            new_sl = min(old_sl, px + k * atr)
    else:
        new_sl = old_sl

    if abs(new_sl - old_sl) > 1e-12:
        conn.execute("UPDATE trades SET sl=? WHERE id=?", (new_sl, tid))
        log_decision(
            source="position_manager",
            decision="TRAIL",
            reason=f"sl {old_sl:.8f}->{new_sl:.8f}",
            symbol=symbol,
            direction=direction,
            risk_state="TRAIL",
            conn=conn,
        )
        log.info("[pm] TRAIL trade#%s sl: %.6f → %.6f", tid, old_sl, new_sl)
    return new_sl


def _ensure_schema() -> None:
    with get_conn() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()]
        if "partial_50_done" not in cols:
            conn.execute("ALTER TABLE trades ADD COLUMN partial_50_done INTEGER NOT NULL DEFAULT 0")
        if "be_done" not in cols:
            conn.execute("ALTER TABLE trades ADD COLUMN be_done INTEGER NOT NULL DEFAULT 0")
        if "rr" not in cols:
            conn.execute("ALTER TABLE trades ADD COLUMN rr REAL")
        # ← Додаємо нові колонки для детального PnL
        if "close_price" not in cols:
            conn.execute("ALTER TABLE trades ADD COLUMN close_price REAL")
        if "close_reason" not in cols:
            conn.execute("ALTER TABLE trades ADD COLUMN close_reason TEXT")
        if "rr_realized" not in cols:
            conn.execute("ALTER TABLE trades ADD COLUMN rr_realized REAL")
        if "pnl_usd" not in cols:
            conn.execute("ALTER TABLE trades ADD COLUMN pnl_usd REAL DEFAULT 0.0")
        if "size_usd" not in cols:
            conn.execute("ALTER TABLE trades ADD COLUMN size_usd REAL DEFAULT 100.0")
        if "closed_at" not in cols:
            conn.execute("ALTER TABLE trades ADD COLUMN closed_at INTEGER")
        if "status" not in cols:
            conn.execute("ALTER TABLE trades ADD COLUMN status TEXT")
        if "initial_sl" not in cols:
            conn.execute("ALTER TABLE trades ADD COLUMN initial_sl REAL")
        conn.execute("UPDATE trades SET initial_sl=sl WHERE initial_sl IS NULL AND sl IS NOT NULL")
        conn.commit()


_ensure_schema()


def manage_open_positions() -> int:
    """
    Менеджмент позицій:
      - при RR ≥ MOVE_SL_TO_BE_AT_RR (1.0) → часткове закриття 50% (з PnL розрахунком) + SL→BE (be_done=1)
      - при RR ≥ 1.5 → м'який трейл (ATR/свінги)
    Повертає кількість оновлених позицій.
    """
    move_be_at = _get_setting_float(
        "move_sl_to_be_at_rr",
        _get_setting_float("move_be_at_rr", 1.0),
    )
    lock_profit_at = _get_setting_float("lock_profit_at_rr", 1.5)
    lock_profit_r = _get_setting_float("lock_profit_r", 0.3)
    trail_after = _get_setting_float("trail_after_rr", 2.0)
    partial_enabled = _get_setting_bool("partial_tp_enabled", True)
    partial_at = _get_setting_float("partial_tp_at_rr", move_be_at)
    partial_pct = _get_setting_float(
        "partial_tp_close_pct",
        _get_setting_float("partial_tp_pct", 0.5),
    )
    partial_pct = max(0.0, min(0.95, partial_pct))

    updated = 0
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, symbol, direction, entry, sl, status, partial_50_done, be_done, initial_sl "
            "FROM trades WHERE (status IS NULL OR UPPER(status)='OPEN')"
        ).fetchall()

        cols_sg = [r[1] for r in conn.execute("PRAGMA table_info(signals)").fetchall()]
        has_trade_id = "trade_id" in cols_sg
        has_atr_entry = "atr_entry" in cols_sg

        def _get_atr_for_trade(tid: int) -> Optional[float]:
            if not (has_trade_id and has_atr_entry):
                return None
            row = conn.execute(
                "SELECT atr_entry FROM signals WHERE trade_id=? ORDER BY id DESC LIMIT 1",
                (tid,),
            ).fetchone()
            return float(row[0]) if row and row[0] is not None else None

        for (tid, symbol, direction, entry, sl, status, partial_done, be_done, initial_sl) in rows:
            try:
                px = _get_price(symbol)
                if px is None:
                    log.debug("[pm] skip %s: no price provider", symbol)
                    continue

                risk_sl = float(initial_sl if initial_sl is not None else sl)
                current_sl = float(sl)
                rr_cur = _rr_current(float(entry), risk_sl, float(px), direction or "LONG")
                changed = False

                # A) partial take-profit
                if partial_enabled and partial_pct > 0 and not partial_done and rr_cur >= partial_at:
                    _close_position_with_pnl(
                        conn, tid, symbol, direction or "LONG",
                        float(entry), risk_sl, float(px),
                        reason="partial_tp", partial_pct=partial_pct
                    )
                    changed = True

                # B) move stop to break-even
                if rr_cur >= move_be_at:
                    if not be_done:
                        if _apply_move_be(conn, tid, symbol, direction or "LONG", current_sl, float(entry)):
                            current_sl = _be_stop(direction or "LONG", current_sl, float(entry))
                        changed = True
                        log.info("[pm] BE move trade#%s %s rr=%.2f", tid, symbol, rr_cur)

                # C) lock a slice of profit after a stronger move
                if lock_profit_at > 0 and lock_profit_r > 0 and rr_cur >= lock_profit_at:
                    if _apply_profit_lock(
                        conn,
                        tid,
                        symbol,
                        direction or "LONG",
                        current_sl,
                        float(entry),
                        risk_sl,
                        lock_profit_r,
                    ):
                        current_sl = _profit_lock_stop(direction or "LONG", float(entry), risk_sl, lock_profit_r)
                        changed = True

                # D) трейл при configured RR
                if trail_after > 0 and rr_cur >= trail_after:
                    atr = _get_atr_for_trade(tid)
                    new_sl = _apply_trail(conn, tid, symbol, direction or "LONG", current_sl, float(px), atr)
                    if abs(new_sl - current_sl) > 1e-12:
                        changed = True

                if changed:
                    updated += 1

            except Exception as e:
                log.warning("[pm] failed trade#%s %s: %s", tid, symbol, e)

        conn.commit()

    return updated

# ← Додаткова функція для мануального закриття з PnL
