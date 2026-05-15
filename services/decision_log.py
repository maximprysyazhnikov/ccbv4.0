"""Structured decision logging for trading candidates."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from utils.db import get_conn

log = logging.getLogger("decision_log")

_SCHEMA_READY = False


def ensure_decision_log_schema(conn=None) -> None:
    """Create decision_log storage if it does not exist."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return

    def _create(active_conn) -> None:
        active_conn.execute(
            """
            CREATE TABLE IF NOT EXISTS decision_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL DEFAULT (datetime('now')),
                source TEXT,
                symbol TEXT,
                timeframe TEXT,
                direction TEXT,
                trade_mode TEXT,
                gate_score INTEGER,
                gate_total INTEGER,
                gate_pct REAL,
                rr REAL,
                decision TEXT NOT NULL,
                reason TEXT,
                risk_state TEXT,
                indicators_json TEXT
            )
            """
        )
        active_conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_decision_log_ts ON decision_log(ts)"
        )
        active_conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_decision_log_symbol ON decision_log(symbol)"
        )
        active_conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_decision_log_decision ON decision_log(decision)"
        )

    if conn is not None:
        _create(conn)
    else:
        with get_conn() as active_conn:
            _create(active_conn)
    _SCHEMA_READY = True


def _safe_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _candidate_value(candidate: Optional[Dict[str, Any]], key: str, fallback: Any = None) -> Any:
    if not candidate:
        return fallback
    value = candidate.get(key)
    return fallback if value in (None, "") else value


def _json_dump(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    except Exception:
        return json.dumps({"repr": repr(value)}, ensure_ascii=False, separators=(",", ":"))


def log_decision(
    *,
    source: str,
    decision: str,
    reason: Optional[str] = None,
    candidate: Optional[Dict[str, Any]] = None,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    direction: Optional[str] = None,
    trade_mode: Optional[str] = None,
    gate_score: Any = None,
    gate_total: Any = None,
    gate_pct: Any = None,
    rr: Any = None,
    risk_state: Optional[str] = None,
    indicators: Any = None,
    conn=None,
) -> None:
    """Write a structured decision row without interrupting the trading loop."""
    try:
        gate_score_value = _safe_int(
            gate_score if gate_score is not None else _candidate_value(candidate, "gate_score")
        )
        gate_total_value = _safe_int(
            gate_total if gate_total is not None else _candidate_value(candidate, "gate_total")
        )
        gate_pct_value = _safe_float(
            gate_pct if gate_pct is not None else _candidate_value(candidate, "gate_pct")
        )
        if gate_pct_value is None and gate_score_value is not None and gate_total_value:
            gate_pct_value = (gate_score_value / gate_total_value) * 100.0

        rr_value = _safe_float(
            rr
            if rr is not None
            else (
                _candidate_value(candidate, "rr_adj")
                or _candidate_value(candidate, "rr_target")
                or _candidate_value(candidate, "rr")
            )
        )
        indicators_value = indicators
        if indicators_value is None and candidate:
            indicators_value = candidate.get("ind")

        row = (
            source,
            str(_candidate_value(candidate, "symbol", symbol) or "").upper() or None,
            str(_candidate_value(candidate, "timeframe", timeframe) or "").lower() or None,
            str(_candidate_value(candidate, "direction", direction) or "").upper() or None,
            trade_mode or _candidate_value(candidate, "trade_mode"),
            gate_score_value,
            gate_total_value,
            gate_pct_value,
            rr_value,
            decision,
            reason,
            risk_state,
            _json_dump(indicators_value),
        )

        def _insert(active_conn) -> None:
            ensure_decision_log_schema(active_conn)
            active_conn.execute(
                """
                INSERT INTO decision_log (
                    source, symbol, timeframe, direction, trade_mode,
                    gate_score, gate_total, gate_pct, rr,
                    decision, reason, risk_state, indicators_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )

        if conn is not None:
            _insert(conn)
        else:
            with get_conn() as active_conn:
                _insert(active_conn)
    except Exception as exc:
        log.debug("[decision_log] write failed: %s", exc)
