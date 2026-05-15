from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from utils.db import get_conn
from utils.settings import get_setting


def _v(row: Any, index: int, default: Any = None) -> Any:
    try:
        return row[index]
    except Exception:
        return default


def _trade_pnl(row: Any, index: int = 0) -> float:
    try:
        return float(_v(row, index, 0.0) or 0.0)
    except Exception:
        return 0.0


def _price(symbol: str) -> Optional[float]:
    try:
        from market_data.binance_data import get_latest_price

        return float(get_latest_price(symbol))
    except Exception:
        pass
    try:
        from services.position_manager import _get_price

        return _get_price(symbol)
    except Exception:
        return None


def _timestamp(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _rr(entry: float, sl: float, px: float, direction: str) -> float:
    risk = abs(entry - sl)
    if risk <= 1e-12:
        return 0.0
    if (direction or "LONG").upper() == "LONG":
        return (px - entry) / risk
    return (entry - px) / risk


def build_daily_risk_report(hours: int = 24) -> str:
    hours = max(1, int(hours or 24))
    low_window = int(get_setting("autopost_low_wr_window", "20") or 20)
    pause_min = float(get_setting("autopost_low_wr_pause_min", "0.20") or 0.20)

    with get_conn() as conn:
        closed = conn.execute(
            """
            SELECT COALESCE(pnl_usd, pnl, 0) AS pnl
              FROM trades
             WHERE status!='OPEN'
               AND (
                    closed_at >= datetime('now', ?)
                    OR CAST(closed_at AS INTEGER) >= CAST(strftime('%s','now', ?) AS INTEGER)
               )
            """,
            (f"-{hours} hours", f"-{hours} hours"),
        ).fetchall()

        wr_rows = conn.execute(
            """
            SELECT COALESCE(pnl_usd, pnl, 0) AS pnl
              FROM trades
             WHERE status!='OPEN'
             ORDER BY id DESC
             LIMIT ?
            """,
            (low_window,),
        ).fetchall()

        skip_rows = conn.execute(
            """
            SELECT decision, COALESCE(reason, '') AS reason, COUNT(*) AS n
              FROM decision_log
             WHERE ts >= datetime('now', ?)
               AND decision NOT IN ('PREPARED', 'SENT', 'OPENED')
             GROUP BY decision, reason
             ORDER BY n DESC, decision
             LIMIT 8
            """,
            (f"-{hours} hours",),
        ).fetchall()

        symbol_rows = conn.execute(
            """
            SELECT symbol, COUNT(*) AS n, ROUND(SUM(COALESCE(pnl_usd, pnl, 0)), 2) AS pnl
              FROM trades
             WHERE status!='OPEN'
               AND symbol IS NOT NULL
               AND (
                    closed_at >= datetime('now', ?)
                    OR CAST(closed_at AS INTEGER) >= CAST(strftime('%s','now', ?) AS INTEGER)
               )
             GROUP BY symbol
             HAVING n > 0
             ORDER BY pnl ASC
            """,
            (f"-{hours} hours", f"-{hours} hours"),
        ).fetchall()

        open_rows = conn.execute(
            """
            SELECT id, symbol, direction, entry, sl, tp, trade_mode
              FROM trades
             WHERE status='OPEN'
             ORDER BY id DESC
             LIMIT 12
            """
        ).fetchall()

    wins = sum(1 for row in closed if _trade_pnl(row) > 0)
    losses = sum(1 for row in closed if _trade_pnl(row) <= 0)
    pnl = sum(_trade_pnl(row) for row in closed)

    if len(wr_rows) >= low_window:
        wr_wins = sum(1 for row in wr_rows if _trade_pnl(row) > 0)
        wr = wr_wins / len(wr_rows)
        mode = "PAUSED" if wr < pause_min else "NORMAL"
        wr_text = f"{wr_wins}/{len(wr_rows)}={wr:.0%}"
    else:
        mode = "WARMUP"
        wr_text = f"need {low_window} closed trades"

    lines = [
        f"Daily risk report ({hours}h)",
        f"Mode: {mode}",
        f"Recent WR: {wr_text}",
        "",
        "Closed trades",
        f"Total: {len(closed)} | Wins: {wins} | Losses: {losses} | PnL: {pnl:+.2f}",
        "",
        "Top skipped/block reasons",
    ]

    if skip_rows:
        for row in skip_rows:
            reason = _v(row, 1, "")
            reason_part = f" - {reason}" if reason else ""
            lines.append(f"- {_v(row, 0)}: {_v(row, 2, 0)}{reason_part}")
    else:
        lines.append("- none")

    lines.extend(["", "Worst symbols"])
    for row in symbol_rows[:5]:
        lines.append(f"- {_v(row, 0)}: {_v(row, 2, 0):+.2f} ({_v(row, 1, 0)} trades)")
    if not symbol_rows:
        lines.append("- none")

    lines.extend(["", "Best symbols"])
    for row in list(reversed(symbol_rows[-5:])):
        lines.append(f"- {_v(row, 0)}: {_v(row, 2, 0):+.2f} ({_v(row, 1, 0)} trades)")
    if not symbol_rows:
        lines.append("- none")

    lines.extend(["", "Open positions"])
    if open_rows:
        for row in open_rows:
            tid, symbol, direction, entry, sl, tp, mode_name = row
            px = _price(symbol)
            rr_text = "n/a"
            if px is not None:
                try:
                    rr_text = f"{_rr(float(entry), float(sl), float(px), direction):+.2f}R"
                except Exception:
                    rr_text = "n/a"
            lines.append(
                f"- #{tid} {symbol} {direction} mode={mode_name or '-'} "
                f"entry={entry} sl={sl} tp={tp} liveRR={rr_text}"
            )
    else:
        lines.append("- none")

    return "\n".join(lines)[:3900]


def build_decision_report(hours: int = 24) -> str:
    hours = max(1, int(hours or 24))
    with get_conn() as conn:
        total_rows = conn.execute(
            "SELECT COUNT(*) FROM decision_log WHERE ts >= datetime('now', ?)",
            (f"-{hours} hours",),
        ).fetchone()
        by_decision = conn.execute(
            """
            SELECT decision, COUNT(*) AS n
              FROM decision_log
             WHERE ts >= datetime('now', ?)
             GROUP BY decision
             ORDER BY n DESC, decision
            """,
            (f"-{hours} hours",),
        ).fetchall()
        skip_reasons = conn.execute(
            """
            SELECT decision, COALESCE(reason, '') AS reason, COUNT(*) AS n
              FROM decision_log
             WHERE ts >= datetime('now', ?)
               AND decision NOT IN ('PREPARED', 'SENT', 'OPENED')
             GROUP BY decision, reason
             ORDER BY n DESC, decision
             LIMIT 10
            """,
            (f"-{hours} hours",),
        ).fetchall()
        blocked_symbols = conn.execute(
            """
            SELECT symbol, decision, COUNT(*) AS n
              FROM decision_log
             WHERE ts >= datetime('now', ?)
               AND symbol IS NOT NULL
               AND decision NOT IN ('PREPARED', 'SENT', 'OPENED')
             GROUP BY symbol, decision
             ORDER BY n DESC, symbol
             LIMIT 10
            """,
            (f"-{hours} hours",),
        ).fetchall()

    total = int(_v(total_rows, 0, 0) or 0)
    counts = {str(_v(row, 0, "")): int(_v(row, 1, 0) or 0) for row in by_decision}
    opened = counts.get("OPENED", 0)
    sent = counts.get("SENT", 0)
    prepared = counts.get("PREPARED", 0)
    paused = counts.get("PAUSED", 0)
    skipped = sum(
        n for decision, n in counts.items() if decision not in {"PREPARED", "SENT", "OPENED"}
    )

    lines = [
        f"Decision report ({hours}h)",
        f"Rows: {total}",
        f"Prepared: {prepared} | Sent: {sent} | Opened: {opened}",
        f"Skipped/blocked/paused: {skipped} | Paused ticks: {paused}",
        "",
        "By decision",
    ]

    if by_decision:
        for row in by_decision:
            lines.append(f"- {_v(row, 0)}: {_v(row, 1, 0)}")
    else:
        lines.append("- none")

    lines.extend(["", "Top skip reasons"])
    if skip_reasons:
        for row in skip_reasons:
            reason = _v(row, 1, "")
            reason_part = f" - {reason}" if reason else ""
            lines.append(f"- {_v(row, 0)}: {_v(row, 2, 0)}{reason_part}")
    else:
        lines.append("- none")

    lines.extend(["", "Most blocked symbols"])
    if blocked_symbols:
        for row in blocked_symbols:
            lines.append(f"- {_v(row, 0)}: {_v(row, 2, 0)} via {_v(row, 1)}")
    else:
        lines.append("- none")

    return "\n".join(lines)[:3900]


def build_open_risk_report() -> str:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, symbol, direction, entry, sl, tp, trade_mode, opened_at
              FROM trades
             WHERE status='OPEN'
             ORDER BY id DESC
            """
        ).fetchall()

    lines = ["Open risk", f"Open positions: {len(rows)}", ""]
    if not rows:
        lines.append("- none")
        return "\n".join(lines)

    no_price = 0
    stale = 0
    now_ts = __import__("time").time()
    for row in rows:
        tid, symbol, direction, entry, sl, tp, mode_name, opened_at = row
        px = _price(symbol)
        rr_text = "n/a"
        if px is None:
            no_price += 1
        else:
            try:
                rr_text = f"{_rr(float(entry), float(sl), float(px), direction):+.2f}R"
            except Exception:
                rr_text = "n/a"
        age_text = "-"
        opened_ts = _timestamp(opened_at)
        if opened_ts is not None:
            try:
                age_sec = now_ts - opened_ts
                age_h = age_sec / 3600
                age_text = f"{age_h:.1f}h"
                if age_h >= 24:
                    stale += 1
            except Exception:
                pass
        marker = ""
        if rr_text == "n/a":
            marker = " no_price"
        elif rr_text.startswith("-"):
            marker = " risk"
        lines.append(
            f"- #{tid} {symbol} {direction} {mode_name or '-'} "
            f"age={age_text} entry={entry} sl={sl} tp={tp} liveRR={rr_text}{marker}"
        )

    lines.extend(["", f"No live price: {no_price}", f"Stale >=24h: {stale}"])
    return "\n".join(lines)[:3900]


def build_edge_report(hours: int = 168) -> str:
    hours = max(1, int(hours or 168))
    with get_conn() as conn:
        decision_rows = conn.execute(
            """
            SELECT decision, risk_state, COUNT(*) AS n
              FROM decision_log
             WHERE ts >= datetime('now', ?)
               AND source IN ('autopost', 'autopost_scan')
             GROUP BY decision, risk_state
             ORDER BY n DESC
             LIMIT 10
            """,
            (f"-{hours} hours",),
        ).fetchall()
        trade_rows = conn.execute(
            """
            SELECT symbol, direction, COUNT(*) AS n,
                   SUM(CASE WHEN COALESCE(pnl_usd, pnl, 0) > 0 THEN 1 ELSE 0 END) AS wins,
                   SUM(COALESCE(rr_realized, rr, 0)) AS sum_r,
                   AVG(COALESCE(rr_realized, rr, 0)) AS avg_r
              FROM trades
             WHERE status!='OPEN'
               AND LOWER(COALESCE(trade_mode, '')) IN ('scalping', 'standard')
               AND (
                    closed_at >= datetime('now', ?)
                    OR CAST(closed_at AS INTEGER) >= CAST(strftime('%s','now', ?) AS INTEGER)
               )
             GROUP BY symbol, direction
             ORDER BY sum_r DESC, n DESC
             LIMIT 12
            """,
            (f"-{hours} hours", f"-{hours} hours"),
        ).fetchall()
        near_rows = conn.execute(
            """
            SELECT symbol, direction, status, COUNT(*) AS n,
                   SUM(CASE WHEN rr_realized > 0 THEN 1 ELSE 0 END) AS wins,
                   SUM(COALESCE(rr_realized, pnl_r, 0)) AS sum_r,
                   AVG(COALESCE(rr_realized, pnl_r, 0)) AS avg_r
              FROM paper_signals
             WHERE source='autopost_near_miss_gate'
               AND opened_at >= datetime('now', ?)
             GROUP BY symbol, direction, status
             ORDER BY status DESC, sum_r DESC, n DESC
             LIMIT 12
            """,
            (f"-{hours} hours",),
        ).fetchall()
        direction_rows = conn.execute(
            """
            WITH recent AS (
                SELECT direction, COALESCE(rr_realized, rr, 0) AS r,
                       ROW_NUMBER() OVER (PARTITION BY direction ORDER BY id DESC) AS rn
                  FROM trades
                 WHERE status!='OPEN'
                   AND LOWER(COALESCE(trade_mode, '')) IN ('scalping', 'standard')
                   AND UPPER(COALESCE(direction, '')) IN ('LONG', 'SHORT')
            )
            SELECT direction, COUNT(*) AS n,
                   SUM(CASE WHEN r > 0 THEN 1 ELSE 0 END) AS wins,
                   SUM(r) AS sum_r,
                   AVG(r) AS avg_r
              FROM recent
             WHERE rn <= ?
             GROUP BY direction
             ORDER BY direction
            """,
            (int(get_setting("direction_edge_guard_lookback", "6") or 6),),
        ).fetchall()
        gate_rows = conn.execute(
            """
            SELECT direction, gate_score, gate_total, COUNT(*) AS n
              FROM decision_log
             WHERE ts >= datetime('now', ?)
               AND source='autopost'
               AND decision='GATE_FAIL'
               AND gate_score IS NOT NULL
               AND gate_total IS NOT NULL
             GROUP BY direction, gate_score, gate_total
             ORDER BY n DESC
             LIMIT 8
            """,
            (f"-{hours} hours",),
        ).fetchall()

    lines = [
        f"Edge report ({hours}h)",
        "Read-only: no thresholds changed.",
        "",
        "Autopost decision mix",
    ]
    if decision_rows:
        for row in decision_rows:
            risk = _v(row, 1, "") or "-"
            lines.append(f"- {_v(row, 0)} / {risk}: {_v(row, 2, 0)}")
    else:
        lines.append("- none")

    guard_enabled = str(get_setting("direction_edge_guard_enabled", "true")).lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    min_trades = int(get_setting("direction_edge_guard_min_trades", "4") or 4)
    min_wr = float(get_setting("direction_edge_guard_min_wr", "0.25") or 0.25)
    min_sum_r = float(get_setting("direction_edge_guard_min_sum_r", "0.0") or 0.0)
    lines.extend(["", "Direction edge guard"])
    lines.append(f"Enabled: {'yes' if guard_enabled else 'no'}")
    if direction_rows:
        for row in direction_rows:
            n = int(_v(row, 1, 0) or 0)
            wins = int(_v(row, 2, 0) or 0)
            wr = (wins / n) if n else 0.0
            sum_r = float(_v(row, 3, 0) or 0.0)
            state = "WATCH"
            if n >= min_trades:
                state = "ALLOW" if (wr >= min_wr and sum_r >= min_sum_r) else "BLOCK"
            lines.append(
                f"- {_v(row, 0)}: {state} {wins}/{n} WR={wr:.0%} "
                f"sumR={sum_r:+.2f} avgR={float(_v(row, 4, 0) or 0):+.2f}"
            )
    else:
        lines.append("- no recent direction data")

    lines.extend(["", "Closed crypto trades by symbol/direction"])
    if trade_rows:
        for row in trade_rows:
            n = int(_v(row, 2, 0) or 0)
            wins = int(_v(row, 3, 0) or 0)
            wr = (wins / n * 100.0) if n else 0.0
            lines.append(
                f"- {_v(row, 0)} {_v(row, 1)}: {wins}/{n} WR={wr:.0f}% "
                f"sumR={float(_v(row, 4, 0) or 0):+.2f} avgR={float(_v(row, 5, 0) or 0):+.2f}"
            )
    else:
        lines.append("- none")

    lines.extend(["", "Near-miss paper gate streams"])
    if near_rows:
        for row in near_rows:
            n = int(_v(row, 3, 0) or 0)
            wins = int(_v(row, 4, 0) or 0)
            wr = (wins / n * 100.0) if n else 0.0
            lines.append(
                f"- {_v(row, 0)} {_v(row, 1)} {str(_v(row, 2, '')).lower()}: "
                f"{wins}/{n} WR={wr:.0f}% sumR={float(_v(row, 5, 0) or 0):+.2f} "
                f"avgR={float(_v(row, 6, 0) or 0):+.2f}"
            )
    else:
        lines.append("- none")

    lines.extend(["", "Most common failed gate buckets"])
    if gate_rows:
        for row in gate_rows:
            score = _v(row, 1, 0)
            total = _v(row, 2, 0)
            try:
                pct = (float(score) / float(total) * 100.0) if float(total) else 0.0
            except Exception:
                pct = 0.0
            lines.append(f"- {_v(row, 0) or '-'} {score}/{total} ({pct:.0f}%): {_v(row, 3, 0)}")
    else:
        lines.append("- none")

    return "\n".join(lines)[:3900]


def build_settings_audit() -> str:
    checks = [
        ("max_open_per_run", "2", "2"),
        ("max_open_per_day", "6", "6"),
        ("metals_max_open_per_day", "33", "33"),
        ("autopost_disable_shorts", "true", "true"),
        ("short_signal_only", "true", "true"),
        ("metals_autopost_open_trades", "false", "false"),
        ("autopost_low_wr_block", "true", "true"),
        ("autopost_low_wr_pause_min", "0.20", "0.20"),
        ("autopost_recovery_wr_min", "0.35", "0.35"),
        ("ev_filter_enabled", "true", "true"),
        ("paper_capture_while_paused", "true", "true"),
        ("autopost_perf_epoch_ts", "", ""),
        ("paper_release_min_closed", "20", "20"),
        ("paper_release_min_wr", "0.35", "0.35"),
        ("paper_release_min_pnl_r", "0.0", "0.0"),
        ("paper_release_min_avg_r", "0.0", "0.0"),
    ]
    with get_conn() as conn:
        rows = []
        for key, recommended, env_name in checks:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            db_value = str(row[0]) if row else None
            env_value = __import__("os").getenv(env_name.upper())
            current = db_value if db_value is not None else env_value
            if key == "autopost_perf_epoch_ts":
                status = "OK" if current not in (None, "") else "OFF"
            else:
                status = "OK" if str(current).lower() == str(recommended).lower() else "CHECK"
            if key in {"max_open_per_run", "max_open_per_day", "metals_max_open_per_day"}:
                try:
                    if int(float(current or 0)) > int(recommended):
                        status = "DANGER"
                except Exception:
                    status = "CHECK"
            rows.append((status, key, current, recommended, db_value, env_value))

    lines = ["Settings audit", ""]
    for status, key, current, recommended, db_value, env_value in rows:
        lines.append(
            f"- {status} {key}: current={current} recommended={recommended} "
            f"(db={db_value}, env={env_value})"
        )
    return "\n".join(lines)[:3900]
