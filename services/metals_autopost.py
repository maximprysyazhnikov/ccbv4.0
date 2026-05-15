from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional

from core_config import CFG
from market_data.metals import collect_metals_scalp_candidates, get_metals_ohlcv, parse_metals
from utils.db import get_conn
from utils.settings import get_setting
from services.decision_log import log_decision
from services.paper_signals import record_paper_signal

log = logging.getLogger("metals_autopost")

TRADE_MODE = "metals_scalping"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _setting_bool(name: str, default: bool) -> bool:
    raw = get_setting(name.lower(), os.getenv(name.upper(), "true" if default else "false"))
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _setting_int(name: str, default: int) -> int:
    raw = get_setting(name.lower(), os.getenv(name.upper(), str(default)))
    try:
        return int(raw or default)
    except (TypeError, ValueError):
        return default


def _round(x: float) -> float:
    return float(f"{float(x):.6f}")


def _table_cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_schema(conn: sqlite3.Connection) -> None:
    try:
        conn.row_factory = sqlite3.Row
    except Exception:
        pass
    for col, typ in [
        ("trade_mode", "TEXT"),
        ("indicators_json", "TEXT"),
        ("gate_score", "INTEGER"),
        ("gate_total", "INTEGER"),
        ("gate_pct", "REAL"),
        ("rr_adj", "REAL"),
        ("rr_raw", "REAL"),
        ("rr_target", "REAL"),
        ("close_price", "REAL"),
        ("close_reason", "TEXT"),
        ("rr_realized", "REAL"),
        ("pnl_usd", "REAL DEFAULT 0.0"),
        ("size_usd", "REAL DEFAULT 100.0"),
        ("fees_bps", "INTEGER DEFAULT 10"),
    ]:
        try:
            conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass
    for col, typ in [
        ("trade_mode", "TEXT"),
        ("trade_id", "INTEGER"),
        ("details", "TEXT"),
        ("closed_at", "TEXT"),
        ("reason_close", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE signals ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass
    try:
        conn.execute("DROP INDEX IF EXISTS uniq_trades_open")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uniq_trades_open_mode "
            "ON trades(symbol, timeframe, trade_mode) WHERE status='OPEN'"
        )
    except Exception:
        pass


def _insert_flexible(conn: sqlite3.Connection, table: str, payload: Dict[str, object]) -> int:
    cols = _table_cols(conn, table)
    keep = {k: v for k, v in payload.items() if k in cols}
    col_list = ", ".join(keep.keys())
    placeholders = ", ".join(["?"] * len(keep))
    cur = conn.execute(
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
        tuple(keep.values()),
    )
    return int(cur.lastrowid)


def _pnl(direction: str, entry: float, price: float, size_usd: float, fees_bps: int) -> tuple[float, float]:
    qty = size_usd / entry if entry else 0.0
    gross = (price - entry) * qty if direction.upper() == "LONG" else (entry - price) * qty
    fees = ((qty * entry) + (qty * price)) * (fees_bps / 10000.0)
    pnl_usd = gross - fees
    return _round(pnl_usd), _round((pnl_usd / size_usd) * 100.0 if size_usd else 0.0)


def _rr_realized(entry: float, sl: float, price: float) -> float:
    risk = abs(entry - sl)
    return float(abs(price - entry) / risk) if risk > 0 else 0.0


def _latest_price(symbol: str, timeframe: str) -> Optional[float]:
    rows = get_metals_ohlcv(symbol, timeframe, 3)
    if not rows:
        return None
    return float(rows[-1]["close"])


def _fmt_price(value: object, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return "-"


def _status_emoji(status: str) -> str:
    s = (status or "").upper()
    if "OPENED" in s:
        return "✅"
    if "ALREADY" in s:
        return "🟡"
    if "SIGNAL" in s:
        return "⚠️"
    return "ℹ️"


def _format_metals_signal(candidate: Dict[str, object], status: str) -> str:
    symbol = str(candidate.get("symbol") or "-")
    timeframe = str(candidate.get("timeframe") or os.getenv("METALS_SCALP_TIMEFRAME", "5m"))
    direction = str(candidate.get("direction") or "-").upper()
    side_emoji = "🟢" if direction == "LONG" else "🔴"
    status_icon = _status_emoji(status)
    gate_score = candidate.get("gate_score", "-")
    gate_total = candidate.get("gate_total", "-")
    gate_pct = float(candidate.get("gate_pct") or 0.0)
    rr = float(candidate.get("rr") or 0.0)
    entry = _fmt_price(candidate.get("entry"))
    sl = _fmt_price(candidate.get("sl"))
    tp = _fmt_price(candidate.get("tp"))
    sl_pct = _fmt_price(candidate.get("sl_pct"), 2)
    tp_pct = _fmt_price(candidate.get("tp_pct"), 2)
    label = str(candidate.get("label") or "Metals")
    hard = candidate.get("hard_blockers") or []
    hard_line = "—" if not hard else "; ".join(str(x) for x in hard[:3])
    ind = candidate.get("ind") if isinstance(candidate.get("ind"), dict) else {}

    lines = [
        f"{side_emoji} *{symbol}* · *{direction}* · `{timeframe}`",
        f"🥇 {label} | ⚖️ RR `{rr:.2f}` | 🚦 Gate `{gate_score}/{gate_total}` ({gate_pct:.0f}%)",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"📍 Entry: `{entry}`",
        f"🛑 SL: `{sl}` ({sl_pct}%)",
        f"🎯 TP: `{tp}` ({tp_pct}%)",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"{status_icon} Status: `{status}`",
    ]
    if ind:
        lines.extend(
            [
                "━━━━━━━━━━━━━━━━━━━━━",
                "📊 Індикатори:",
                f"EMA50/200: `{_fmt_price(ind.get('ema50'))}` / `{_fmt_price(ind.get('ema200'))}`",
                f"RSI14: `{_fmt_price(ind.get('rsi14'), 1)}` | ADX14: `{_fmt_price(ind.get('adx14'), 1)}`",
                f"StochRSI: K=`{_fmt_price(ind.get('stoch_k'), 1)}` D=`{_fmt_price(ind.get('stoch_d'), 1)}`",
                f"BB%B: `{_fmt_price(ind.get('bb_pct_b'), 2)}` | Vol: `{_fmt_price(ind.get('vol_ratio'), 2)}x`",
            ]
        )
    if hard:
        lines.extend(["🧱 Blockers:", f"`{hard_line}`"])
    return "\n".join(lines)


def _close_trade_row(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    price: float,
    reason: str,
    win_loss_hint: Optional[str] = None,
) -> int:
    size_usd = float(row["size_usd"] or CFG.get("sim_usd_per_trade", 100) or 100)
    fees_bps = int(row["fees_bps"] or CFG.get("fees_bps", 10) or 10)
    entry = float(row["entry"])
    sl = float(row["sl"])
    pnl_usd, pnl_pct = _pnl(str(row["direction"]), entry, price, size_usd, fees_bps)
    rr_real = _rr_realized(entry, sl, price)
    status = win_loss_hint if win_loss_hint in ("WIN", "LOSS") else ("WIN" if pnl_usd > 0 else "LOSS")
    conn.execute(
        """
        UPDATE trades
           SET status=?,
               closed_at=?,
               close_price=?,
               close_reason=?,
               pnl_usd=?,
               rr_realized=?
         WHERE id=?
        """,
        (status, _now_iso(), _round(price), reason, pnl_usd, rr_real, int(row["id"])),
    )
    if row["signal_id"]:
        conn.execute(
            "UPDATE signals SET status=?, closed_at=?, reason_close=? WHERE id=?",
            (status, _now_iso(), reason, int(row["signal_id"])),
        )
    return int(row["id"])


def _open_metals_trade(conn: sqlite3.Connection, candidate: Dict[str, object], user_id: int) -> Optional[int]:
    symbol = str(candidate["symbol"]).upper()
    timeframe = str(candidate["timeframe"])
    direction = str(candidate["direction"]).upper()
    open_rows = conn.execute(
        "SELECT * FROM trades WHERE symbol=? AND timeframe=? AND trade_mode=? AND status='OPEN' ORDER BY id",
        (symbol, timeframe, TRADE_MODE),
    ).fetchall()
    for row in open_rows:
        if str(row["direction"]).upper() == direction:
            log.info("[metals] already open same dir %s/%s %s", symbol, timeframe, direction)
            return None
        _close_trade_row(conn, row, float(candidate["entry"]), "REVERSED")

    details = json.dumps(
        {
            "trade_mode": TRADE_MODE,
            "label": candidate.get("label"),
            "yahoo_symbol": candidate.get("yahoo_symbol"),
            "hard_blockers": candidate.get("hard_blockers"),
            "ind": candidate.get("ind"),
        },
        ensure_ascii=False,
    )
    signal_id = _insert_flexible(
        conn,
        "signals",
        {
            "user_id": int(user_id),
            "symbol": symbol,
            "timeframe": timeframe,
            "tf": timeframe,
            "direction": direction,
            "entry": float(candidate["entry"]),
            "sl": float(candidate["sl"]),
            "tp": float(candidate["tp"]),
            "rr": float(candidate["rr"]),
            "source": "metals_autopost",
            "status": "OPEN",
            "opened_at": _now_iso(),
            "ts_created": int(datetime.now(timezone.utc).timestamp()),
            "trade_mode": TRADE_MODE,
            "details": details,
        },
    )
    trade_id = _insert_flexible(
        conn,
        "trades",
        {
            "signal_id": signal_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "direction": direction,
            "entry": _round(float(candidate["entry"])),
            "sl": _round(float(candidate["sl"])),
            "tp": _round(float(candidate["tp"])),
            "opened_at": _now_iso(),
            "size_usd": float(CFG.get("sim_usd_per_trade", 100) or 100),
            "fees_bps": int(CFG.get("fees_bps", 10) or 10),
            "rr_planned": float(candidate["rr"]),
            "rr": float(candidate["rr"]),
            "status": "OPEN",
            "trade_mode": TRADE_MODE,
            "indicators_json": details,
            "gate_score": int(candidate["gate_score"]),
            "gate_total": int(candidate["gate_total"]),
            "gate_pct": float(candidate["gate_pct"]),
            "rr_raw": float(candidate["rr"]),
            "rr_adj": float(candidate["rr"]),
            "rr_target": float(candidate["rr"]),
            "ema50": (candidate.get("ind") or {}).get("ema50") if isinstance(candidate.get("ind"), dict) else None,
            "ema200": (candidate.get("ind") or {}).get("ema200") if isinstance(candidate.get("ind"), dict) else None,
            "atr_entry": (candidate.get("ind") or {}).get("atr14") if isinstance(candidate.get("ind"), dict) else None,
        },
    )
    if "trade_id" in _table_cols(conn, "signals"):
        conn.execute("UPDATE signals SET trade_id=? WHERE id=?", (trade_id, signal_id))
    log.info("[metals] OPEN trade#%s %s/%s %s", trade_id, symbol, timeframe, direction)
    return trade_id


def close_metals_trades_once() -> int:
    updated = 0
    with get_conn() as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT * FROM trades WHERE status='OPEN' AND trade_mode=?",
            (TRADE_MODE,),
        ).fetchall()
        for row in rows:
            try:
                price = _latest_price(str(row["symbol"]), str(row["timeframe"]))
            except Exception as exc:
                log.warning("[metals] price failed for %s/%s: %s", row["symbol"], row["timeframe"], exc)
                continue
            if price is None:
                continue
            direction = str(row["direction"]).upper()
            sl = float(row["sl"])
            tp = float(row["tp"])
            reason = None
            hint = None
            if direction == "LONG":
                if price <= sl:
                    reason, hint = "SL", "LOSS"
                elif price >= tp:
                    reason, hint = "TP", "WIN"
            else:
                if price >= sl:
                    reason, hint = "SL", "LOSS"
                elif price <= tp:
                    reason, hint = "TP", "WIN"
            if reason:
                _close_trade_row(conn, row, price, reason, hint)
                updated += 1
    if updated:
        log.info("[metals] closed %d trade(s)", updated)
    return updated


def run_metals_autopost_once() -> List[str]:
    if not _env_bool("METALS_AUTOPOST_ENABLED", True):
        return []
    user_id = int(os.getenv("TELEGRAM_CHAT_ID", "0") or 0)
    timeframe = os.getenv("METALS_SCALP_TIMEFRAME", "5m")
    symbols = parse_metals(",".join(CFG.get("metals_symbols", []) or []))
    candidates = collect_metals_scalp_candidates(symbols=symbols, timeframe=timeframe)
    final_candidates = [c for c in candidates if c.get("ok") and c.get("final")]
    if _setting_bool("AUTOPOST_DISABLE_SHORTS", False):
        final_candidates = [c for c in final_candidates if str(c.get("direction", "")).upper() != "SHORT"]
    messages: List[str] = []
    with get_conn() as conn:
        _ensure_schema(conn)
        open_trades = _setting_bool("METALS_AUTOPOST_OPEN_TRADES", True)
        max_open_per_day = _setting_int("METALS_MAX_OPEN_PER_DAY", 33)
        daily_opened = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM trades
                 WHERE opened_at >= datetime('now', '-24 hours')
                   AND LOWER(COALESCE(trade_mode, '')) = ?
                """,
                (TRADE_MODE,),
            ).fetchone()[0] or 0
        )
        opened_run = 0
        for c in final_candidates:
            symbol = str(c["symbol"])
            direction = str(c["direction"])
            if open_trades and (daily_opened + opened_run) >= max_open_per_day:
                trade_id = None
                status = "SKIPPED (day limit)"
                decision = "LIMIT_REACHED"
                reason = f"metals_day_limit {daily_opened + opened_run}/{max_open_per_day}"
            else:
                trade_id = _open_metals_trade(conn, c, user_id) if open_trades else None
                if trade_id:
                    opened_run += 1
                    status = f"OPENED id={trade_id}"
                    decision = "OPENED"
                    reason = f"trade_id={trade_id}"
                elif open_trades:
                    status = "ALREADY OPEN / SKIPPED"
                    decision = "BRIDGE_SKIP"
                    reason = "already_open_or_skipped"
                else:
                    status = "SIGNAL ONLY"
                    decision = "METALS_SIGNAL_ONLY"
                    reason = "METALS_AUTOPOST_OPEN_TRADES=false"
                    record_paper_signal(
                        source="metals_signal_only",
                        symbol=symbol,
                        timeframe=str(c.get("timeframe") or timeframe),
                        direction=direction,
                        trade_mode=TRADE_MODE,
                        entry=c.get("entry"),
                        sl=c.get("sl"),
                        tp=c.get("tp"),
                        rr=c.get("rr"),
                        reason=reason,
                        indicators=c,
                        conn=conn,
                    )
            log_decision(
                source="metals_autopost",
                decision=decision,
                reason=reason,
                symbol=symbol,
                timeframe=str(c.get("timeframe") or timeframe),
                direction=direction,
                trade_mode=TRADE_MODE,
                gate_score=c.get("gate_score"),
                gate_total=c.get("gate_total"),
                gate_pct=c.get("gate_pct"),
                rr=c.get("rr"),
                risk_state=status,
                indicators=c,
                conn=conn,
            )
            messages.append(_format_metals_signal(c, status))
    log.info("[metals] candidates=%d final=%d messages=%d", len(candidates), len(final_candidates), len(messages))
    return messages


def metals_kpi_summary(days: int = 7) -> str:
    since = datetime.now(timezone.utc).timestamp() - days * 24 * 3600
    with get_conn() as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT symbol,
                   COUNT(*) AS n,
                   SUM(CASE WHEN COALESCE(pnl_usd,0)>0 THEN 1 ELSE 0 END) AS wins,
                   ROUND(AVG(COALESCE(rr_realized, rr_planned, rr, 0)), 2) AS avg_rr,
                   ROUND(SUM(COALESCE(pnl_usd,0)), 2) AS pnl
              FROM trades
             WHERE trade_mode=?
               AND status <> 'OPEN'
               AND CAST(strftime('%s', closed_at) AS INTEGER) >= ?
             GROUP BY symbol
             ORDER BY symbol
            """,
            (TRADE_MODE, int(since)),
        ).fetchall()
        open_rows = conn.execute(
            "SELECT symbol, direction, entry, sl, tp, opened_at FROM trades WHERE trade_mode=? AND status='OPEN' ORDER BY symbol",
            (TRADE_MODE,),
        ).fetchall()
    head = f"🥇 Metals KPI за {days}д"
    if not rows and not open_rows:
        return head + "\n— немає metals-угод за період."
    out = [head, "────────────────────────────", "Символ   Угод WR%  AvgRR     PnL"]
    total_n = total_wins = 0
    total_pnl = 0.0
    for r in rows:
        n = int(r["n"] or 0)
        wins = int(r["wins"] or 0)
        wr = (wins / n * 100.0) if n else 0.0
        pnl = float(r["pnl"] or 0.0)
        total_n += n
        total_wins += wins
        total_pnl += pnl
        out.append(f"{r['symbol']:7} {n:4} {wr:5.1f} {float(r['avg_rr'] or 0):6.2f} {pnl:7.2f}")
    if rows:
        wr_total = (total_wins / total_n * 100.0) if total_n else 0.0
        out.append("────────────────────────────")
        out.append(f"ВСЬОГО  {total_n:4} {wr_total:5.1f}        {total_pnl:7.2f}")
    if open_rows:
        out.append("")
        out.append(f"Відкриті metals-ордери: {len(open_rows)}")
        for r in open_rows[:8]:
            out.append(f"{r['symbol']} {r['direction']} @ {float(r['entry']):.2f}")
    return "\n".join(out)
