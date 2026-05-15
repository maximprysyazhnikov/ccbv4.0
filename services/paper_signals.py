from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from utils.db import get_conn
from utils.settings import get_setting

log = logging.getLogger("paper_signals")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    except Exception:
        return json.dumps({"repr": repr(value)}, ensure_ascii=False, separators=(",", ":"))


def ensure_paper_signals_schema(conn=None) -> None:
    def _create(active_conn) -> None:
        active_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                source TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                direction TEXT NOT NULL,
                trade_mode TEXT,
                entry REAL NOT NULL,
                sl REAL NOT NULL,
                tp REAL,
                rr REAL,
                status TEXT NOT NULL DEFAULT 'OPEN',
                reason TEXT,
                close_reason TEXT,
                close_price REAL,
                rr_realized REAL,
                pnl_r REAL,
                indicators_json TEXT
            )
            """
        )
        active_conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uniq_paper_signals_open
            ON paper_signals(source, symbol, timeframe, direction)
            WHERE status='OPEN'
            """
        )
        active_conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_paper_signals_status ON paper_signals(status)"
        )
        active_conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_paper_signals_symbol ON paper_signals(symbol)"
        )

    if conn is not None:
        _create(conn)
    else:
        with get_conn() as active_conn:
            _create(active_conn)


def record_paper_signal(
    *,
    source: str,
    symbol: str,
    timeframe: str,
    direction: str,
    entry: Any,
    sl: Any,
    tp: Any = None,
    rr: Any = None,
    trade_mode: Optional[str] = None,
    reason: Optional[str] = None,
    indicators: Any = None,
    conn=None,
) -> Optional[int]:
    try:
        row = (
            _now_iso(),
            source,
            str(symbol).upper(),
            str(timeframe).lower(),
            str(direction).upper(),
            trade_mode,
            float(entry),
            float(sl),
            float(tp) if tp is not None else None,
            float(rr) if rr not in (None, "") else None,
            reason,
            _json(indicators),
        )

        def _insert(active_conn) -> Optional[int]:
            ensure_paper_signals_schema(active_conn)
            existing = active_conn.execute(
                """
                SELECT id FROM paper_signals
                 WHERE source=? AND symbol=? AND timeframe=? AND direction=? AND status='OPEN'
                 LIMIT 1
                """,
                (row[1], row[2], row[3], row[4]),
            ).fetchone()
            if existing:
                try:
                    return int(existing["id"])
                except Exception:
                    return int(existing[0])
            cur = active_conn.execute(
                """
                INSERT INTO paper_signals (
                    opened_at, source, symbol, timeframe, direction, trade_mode,
                    entry, sl, tp, rr, reason, indicators_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
            return int(cur.lastrowid)

        if conn is not None:
            return _insert(conn)
        with get_conn() as active_conn:
            return _insert(active_conn)
    except Exception as exc:
        log.debug("[paper] record failed: %s", exc)
        return None


def _price(symbol: str) -> Optional[float]:
    try:
        from market_data.binance_data import get_latest_price

        return float(get_latest_price(symbol))
    except Exception:
        try:
            from services.position_manager import _get_price

            return _get_price(symbol)
        except Exception:
            return None


def _rr(entry: float, sl: float, close_price: float, direction: str) -> float:
    risk = abs(entry - sl)
    if risk <= 1e-12:
        return 0.0
    if direction.upper() == "LONG":
        return (close_price - entry) / risk
    return (entry - close_price) / risk


def close_paper_signals_once(price_overrides: Optional[Dict[str, float]] = None) -> int:
    updated = 0
    with get_conn() as conn:
        ensure_paper_signals_schema(conn)
        rows = conn.execute(
            """
            SELECT id, symbol, direction, entry, sl, tp
              FROM paper_signals
             WHERE status='OPEN'
            """
        ).fetchall()
        for row in rows:
            try:
                tid = int(row["id"] if hasattr(row, "keys") else row[0])
                symbol = str(row["symbol"] if hasattr(row, "keys") else row[1])
                direction = str(row["direction"] if hasattr(row, "keys") else row[2]).upper()
                entry = float(row["entry"] if hasattr(row, "keys") else row[3])
                sl = float(row["sl"] if hasattr(row, "keys") else row[4])
                tp_raw = row["tp"] if hasattr(row, "keys") else row[5]
                tp = float(tp_raw) if tp_raw is not None else None
                price = float(price_overrides[symbol]) if price_overrides and symbol in price_overrides else _price(symbol)
                if price is None or tp is None:
                    continue

                close_reason = None
                if direction == "LONG":
                    if price <= sl:
                        close_reason = "SL"
                    elif price >= tp:
                        close_reason = "TP"
                else:
                    if price >= sl:
                        close_reason = "SL"
                    elif price <= tp:
                        close_reason = "TP"
                if not close_reason:
                    continue

                rr_realized = _rr(entry, sl, price, direction)
                conn.execute(
                    """
                    UPDATE paper_signals
                       SET status='CLOSED',
                           closed_at=?,
                           close_reason=?,
                           close_price=?,
                           rr_realized=?,
                           pnl_r=?
                     WHERE id=?
                    """,
                    (_now_iso(), close_reason, price, rr_realized, rr_realized, tid),
                )
                updated += 1
            except Exception as exc:
                log.debug("[paper] close failed: %s", exc)
    if updated:
        log.info("[paper] closed %d paper signal(s)", updated)
    return updated


def build_paper_report(hours: int = 168) -> str:
    hours = max(1, int(hours or 168))
    with get_conn() as conn:
        ensure_paper_signals_schema(conn)
        rows = conn.execute(
            """
            SELECT source, trade_mode, direction, symbol,
                   COUNT(*) AS n,
                   SUM(CASE WHEN close_reason='TP' THEN 1 ELSE 0 END) AS wins,
                   ROUND(SUM(COALESCE(pnl_r, 0)), 2) AS pnl_r,
                   ROUND(AVG(COALESCE(rr_realized, 0)), 2) AS avg_r
              FROM paper_signals
             WHERE opened_at >= datetime('now', ?)
             GROUP BY source, trade_mode, direction, symbol
             ORDER BY pnl_r DESC, n DESC
             LIMIT 20
            """,
            (f"-{hours} hours",),
        ).fetchall()
        open_count = conn.execute(
            "SELECT COUNT(*) FROM paper_signals WHERE status='OPEN'"
        ).fetchone()[0]
        closed_count = conn.execute(
            "SELECT COUNT(*) FROM paper_signals WHERE status='CLOSED' AND opened_at >= datetime('now', ?)",
            (f"-{hours} hours",),
        ).fetchone()[0]

    lines = [
        f"Paper report ({hours}h)",
        f"Open paper: {open_count} | Closed in window: {closed_count}",
        "",
        "Source/mode/direction/symbol",
    ]
    if not rows:
        lines.append("- no paper signals yet")
    for row in rows:
        n = int(row["n"] if hasattr(row, "keys") else row[4])
        wins = int(row["wins"] if hasattr(row, "keys") else row[5])
        wr = (wins / n * 100.0) if n else 0.0
        source = row["source"] if hasattr(row, "keys") else row[0]
        mode = row["trade_mode"] if hasattr(row, "keys") else row[1]
        direction = row["direction"] if hasattr(row, "keys") else row[2]
        symbol = row["symbol"] if hasattr(row, "keys") else row[3]
        pnl_r = row["pnl_r"] if hasattr(row, "keys") else row[6]
        avg_r = row["avg_r"] if hasattr(row, "keys") else row[7]
        lines.append(
            f"- {source}/{mode or '-'} {direction} {symbol}: "
            f"{wins}/{n} WR={wr:.0f}% pnlR={float(pnl_r or 0):+.2f} avgR={float(avg_r or 0):+.2f}"
        )
    return "\n".join(lines)[:3900]


def _setting_int(key: str, default: int) -> int:
    try:
        return int(float(get_setting(key, str(default)) or default))
    except Exception:
        return default


def _setting_float(key: str, default: float) -> float:
    try:
        return float(get_setting(key, str(default)) or default)
    except Exception:
        return default


def evaluate_release_streams(hours: int = 168) -> list[dict[str, Any]]:
    hours = max(1, int(hours or 168))
    min_closed = _setting_int("paper_release_min_closed", 20)
    min_wr = _setting_float("paper_release_min_wr", 0.35)
    min_pnl_r = _setting_float("paper_release_min_pnl_r", 0.0)
    min_avg_r = _setting_float("paper_release_min_avg_r", 0.0)

    with get_conn() as conn:
        ensure_paper_signals_schema(conn)
        rows = conn.execute(
            """
            SELECT source, trade_mode, direction, symbol,
                   COUNT(*) AS closed_n,
                   SUM(CASE WHEN close_reason='TP' THEN 1 ELSE 0 END) AS wins,
                   SUM(COALESCE(pnl_r, 0)) AS pnl_r,
                   AVG(COALESCE(rr_realized, 0)) AS avg_r
              FROM paper_signals
             WHERE status='CLOSED'
               AND closed_at >= datetime('now', ?)
             GROUP BY source, trade_mode, direction, symbol
             ORDER BY pnl_r DESC, closed_n DESC
            """,
            (f"-{hours} hours",),
        ).fetchall()

    out: list[dict[str, Any]] = []
    for row in rows:
        source = row["source"] if hasattr(row, "keys") else row[0]
        mode = row["trade_mode"] if hasattr(row, "keys") else row[1]
        direction = row["direction"] if hasattr(row, "keys") else row[2]
        symbol = row["symbol"] if hasattr(row, "keys") else row[3]
        closed_n = int((row["closed_n"] if hasattr(row, "keys") else row[4]) or 0)
        wins = int((row["wins"] if hasattr(row, "keys") else row[5]) or 0)
        pnl_r = float((row["pnl_r"] if hasattr(row, "keys") else row[6]) or 0.0)
        avg_r = float((row["avg_r"] if hasattr(row, "keys") else row[7]) or 0.0)
        wr = wins / closed_n if closed_n else 0.0
        reasons = []
        if closed_n < min_closed:
            reasons.append(f"closed {closed_n}<{min_closed}")
        if wr < min_wr:
            reasons.append(f"WR {wr:.0%}<{min_wr:.0%}")
        if pnl_r <= min_pnl_r:
            reasons.append(f"pnlR {pnl_r:+.2f}<={min_pnl_r:+.2f}")
        if avg_r <= min_avg_r:
            reasons.append(f"avgR {avg_r:+.2f}<={min_avg_r:+.2f}")
        if not reasons:
            status = "ELIGIBLE"
        elif closed_n >= max(3, min_closed // 2) and pnl_r > min_pnl_r:
            status = "WATCH"
        else:
            status = "BLOCKED"
        out.append(
            {
                "source": source,
                "trade_mode": mode,
                "direction": direction,
                "symbol": symbol,
                "closed_n": closed_n,
                "wins": wins,
                "wr": wr,
                "pnl_r": pnl_r,
                "avg_r": avg_r,
                "status": status,
                "reasons": reasons,
                "thresholds": {
                    "min_closed": min_closed,
                    "min_wr": min_wr,
                    "min_pnl_r": min_pnl_r,
                    "min_avg_r": min_avg_r,
                },
            }
        )
    return out


def build_release_report(hours: int = 168, write_audit: bool = False) -> str:
    streams = evaluate_release_streams(hours)
    min_closed = _setting_int("paper_release_min_closed", 20)
    min_wr = _setting_float("paper_release_min_wr", 0.35)
    min_pnl_r = _setting_float("paper_release_min_pnl_r", 0.0)
    min_avg_r = _setting_float("paper_release_min_avg_r", 0.0)

    if write_audit:
        try:
            from services.decision_log import log_decision

            for item in streams[:30]:
                log_decision(
                    source="release_engine",
                    decision=f"RELEASE_{item['status']}",
                    reason=(
                        f"{item['source']}/{item['trade_mode']}/{item['direction']}/{item['symbol']} "
                        f"n={item['closed_n']} WR={item['wr']:.0%} pnlR={item['pnl_r']:+.2f} "
                        f"avgR={item['avg_r']:+.2f}; thresholds n>={min_closed} "
                        f"WR>={min_wr:.0%} pnlR>{min_pnl_r:+.2f} avgR>{min_avg_r:+.2f}; "
                        f"reasons={'; '.join(item['reasons']) or 'ok'}"
                    ),
                    symbol=item["symbol"],
                    direction=item["direction"],
                    trade_mode=item["trade_mode"],
                    risk_state=item["status"],
                )
        except Exception as exc:
            log.debug("[paper] release audit failed: %s", exc)

    lines = [
        f"Release report ({hours}h)",
        f"Thresholds: closed>={min_closed}, WR>={min_wr:.0%}, pnlR>{min_pnl_r:+.2f}, avgR>{min_avg_r:+.2f}",
        "Read-only: live opening settings are not changed.",
        "",
    ]
    if not streams:
        lines.append("No closed paper streams yet.")
        return "\n".join(lines)

    for title, status in (("Eligible", "ELIGIBLE"), ("Watch", "WATCH"), ("Blocked", "BLOCKED")):
        lines.append(title)
        items = [item for item in streams if item["status"] == status]
        if not items:
            lines.append("- none")
        for item in items[:8]:
            reasons = "; ".join(item["reasons"]) if item["reasons"] else "ok"
            lines.append(
                f"- {item['source']}/{item['trade_mode'] or '-'} {item['direction']} {item['symbol']}: "
                f"{item['wins']}/{item['closed_n']} WR={item['wr']:.0%} "
                f"pnlR={item['pnl_r']:+.2f} avgR={item['avg_r']:+.2f} | {reasons}"
            )
        lines.append("")
    return "\n".join(lines)[:3900]


def build_allowlist_proposal(hours: int = 168, write_audit: bool = False) -> str:
    streams = evaluate_release_streams(hours)
    eligible = [item for item in streams if item["status"] == "ELIGIBLE"]

    if write_audit:
        try:
            from services.decision_log import log_decision

            decision = "ALLOWLIST_PROPOSAL_READY" if eligible else "ALLOWLIST_PROPOSAL_EMPTY"
            reason = (
                f"eligible={len(eligible)}; "
                + ", ".join(
                    f"{item['source']}/{item['trade_mode']}/{item['direction']}/{item['symbol']}"
                    for item in eligible[:10]
                )
            )
            log_decision(
                source="allowlist_proposal",
                decision=decision,
                reason=reason,
                risk_state="READ_ONLY",
            )
        except Exception as exc:
            log.debug("[paper] allowlist proposal audit failed: %s", exc)

    lines = [
        f"Allowlist proposal ({hours}h)",
        "Read-only: no live settings are changed.",
        "",
    ]

    if not eligible:
        lines.extend(
            [
                "No eligible streams yet.",
                "Need each stream to pass release thresholds before proposal:",
                f"- closed >= {_setting_int('paper_release_min_closed', 20)}",
                f"- WR >= {_setting_float('paper_release_min_wr', 0.35):.0%}",
                f"- pnlR > {_setting_float('paper_release_min_pnl_r', 0.0):+.2f}",
                f"- avgR > {_setting_float('paper_release_min_avg_r', 0.0):+.2f}",
            ]
        )
        return "\n".join(lines)[:3900]

    lines.extend(
        [
            "Proposed recovery allowlist",
            "Scope: signal/recovery-only review. Do not auto-open from this proposal.",
        ]
    )
    for item in eligible[:12]:
        lines.append(
            f"- {item['source']}/{item['trade_mode'] or '-'} {item['direction']} {item['symbol']}: "
            f"{item['wins']}/{item['closed_n']} WR={item['wr']:.0%} "
            f"pnlR={item['pnl_r']:+.2f} avgR={item['avg_r']:+.2f}"
        )

    symbols = sorted({item["symbol"] for item in eligible})
    directions = sorted({item["direction"] for item in eligible})
    modes = sorted({str(item["trade_mode"] or "-") for item in eligible})
    lines.extend(
        [
            "",
            "Candidate config proposal",
            "Symbols: " + ",".join(symbols),
            "Directions: " + ",".join(directions),
            "Modes: " + ",".join(modes),
            "Next manual action: review this list, then decide whether to add a recovery allowlist config.",
        ]
    )
    return "\n".join(lines)[:3900]


def get_allowlist_proposal_state(hours: int = 168) -> dict[str, Any]:
    eligible = [item for item in evaluate_release_streams(hours) if item["status"] == "ELIGIBLE"]
    parts = []
    for item in eligible:
        parts.append(
            ":".join(
                [
                    str(item["source"]),
                    str(item["trade_mode"] or "-"),
                    str(item["direction"]),
                    str(item["symbol"]),
                    str(item["closed_n"]),
                    str(item["wins"]),
                    f"{item['pnl_r']:.2f}",
                    f"{item['avg_r']:.2f}",
                ]
            )
        )
    key = "|".join(sorted(parts))
    return {"eligible": eligible, "key": key}
