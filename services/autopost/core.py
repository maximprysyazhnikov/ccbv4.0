"""Core autopost execution logic."""
from __future__ import annotations

import os
import logging
import math
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardMarkup, InlineKeyboardButton

from utils.settings import get_setting
from utils.db import get_conn
from core_config import CFG
from utils.user_settings import get_user_settings
from services.autopost.formatting import (
    safe_float, format_message_text
)
from services.autopost.persistence import (
    seen_recently, reserve_autopost_send, complete_autopost_send
)
from services.autopost.indicators import ind_summary, build_panel_lite
from services.autopost.scoring import compute_rr_num, quick_qscore
from services.autopost.orderbook import get_wall_info
from services.decision_log import log_decision
from services.paper_signals import record_paper_signal

log = logging.getLogger("autopost")

try:
    from services.trade_engine import open_trade_from_signal
except ImportError:
    def open_trade_from_signal(signal):
        log.warning("[autopost] trade_engine.open_trade_from_signal not available")
        return None

try:
    from services.analyzer_core import compute_rr_metrics
except Exception:
    def compute_rr_metrics(entry: float, sl: float, tp: Optional[float]):
        rr_eps = 1e-6
        dist = abs(entry - sl)
        # Use abs() for direction-agnostic RR calculation
        rr_t = (abs(tp - entry) / dist) if (tp is not None and dist > rr_eps) else None
        return {"entry_sl_dist": dist, "rr_target": rr_t}


def _parse_csv_set(raw: Any) -> Set[str]:
    if raw in (None, ""):
        return set()
    return {token.strip().upper() for token in str(raw).split(",") if token.strip()}


def _parse_hour_set(raw: Any) -> Set[int]:
    hours: Set[int] = set()
    if raw in (None, ""):
        return hours
    for token in str(raw).split(","):
        token = token.strip()
        if not token:
            continue
        try:
            hour = int(token)
        except ValueError:
            continue
        if 0 <= hour <= 23:
            hours.add(hour)
    return hours


def _setting_bool(key: str, default: bool = False) -> bool:
    raw = get_setting(key.lower(), os.getenv(key.upper(), "true" if default else "false"))
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _setting_float(key: str, default: float) -> float:
    raw = get_setting(key.lower(), os.getenv(key.upper(), str(default)))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _setting_int(key: str, default: int) -> int:
    raw = get_setting(key.lower(), os.getenv(key.upper(), str(default)))
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


def _perf_epoch_ts() -> Optional[int]:
    raw = get_setting("autopost_perf_epoch_ts", os.getenv("AUTOPOST_PERF_EPOCH_TS", ""))
    if raw in (None, ""):
        return None
    try:
        value = int(float(raw))
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def _parse_closed_ts(raw: Any) -> Optional[float]:
    if raw in (None, ""):
        return None
    text = str(raw).strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.timestamp()
    except ValueError:
        return None


def _row_value(row: Any, key: str, index: int, default: Any = None) -> Any:
    try:
        return row[key]
    except Exception:
        try:
            return row[index]
        except Exception:
            return default


def _trade_pnl(row: Any) -> float:
    try:
        pnl_usd = _row_value(row, "pnl_usd", 0)
        pnl = _row_value(row, "pnl", 1)
        return float(pnl_usd if pnl_usd is not None else (pnl or 0.0))
    except Exception:
        return 0.0


def _global_low_wr_block_reason(conn) -> Optional[str]:
    if not _setting_bool("autopost_low_wr_block", True):
        return None
    window = _setting_int("autopost_low_wr_window", 20)
    pause_wr = _setting_float("autopost_low_wr_pause_min", 0.20)
    if window <= 0:
        return None
    epoch_ts = _perf_epoch_ts()
    epoch_filter = ""
    params: List[Any] = []
    if epoch_ts:
        epoch_filter = """
           AND (
                (closed_at GLOB '[0-9]*' AND CAST(closed_at AS INTEGER) >= ?)
                OR closed_at >= datetime(?, 'unixepoch')
           )
        """
        params.extend([epoch_ts, epoch_ts])
    rows = conn.execute(
        f"""
        SELECT pnl_usd, pnl
          FROM trades
         WHERE status!='OPEN'
         {epoch_filter}
         ORDER BY id DESC
         LIMIT ?
        """,
        (*params, window),
    ).fetchall()
    if len(rows) < window:
        if epoch_ts:
            log.info("[autopost] perf_epoch warmup: closed %d/%d since reset", len(rows), window)
        return None
    wins = sum(1 for row in rows if _trade_pnl(row) > 0)
    wr = wins / len(rows)
    if wr < pause_wr:
        return f"low_wr {wins}/{len(rows)}={wr:.0%} < pause {pause_wr:.0%}"
    return None


def _symbol_risk_block_reason(conn, symbol: str) -> Optional[str]:
    cooldown_min = _setting_int("symbol_cooldown_after_sl_min", 60)
    if cooldown_min > 0:
        row = conn.execute(
            """
            SELECT close_reason, reason_close, closed_at
              FROM trades
             WHERE symbol=? AND status!='OPEN'
             ORDER BY id DESC
             LIMIT 1
            """,
            (symbol,),
        ).fetchone()
        if row:
            reason = str(_row_value(row, "close_reason", 0) or _row_value(row, "reason_close", 1) or "").upper()
            closed_ts = _parse_closed_ts(_row_value(row, "closed_at", 2))
            if reason == "SL" and closed_ts is not None:
                age_min = (time.time() - closed_ts) / 60.0
                if age_min < cooldown_min:
                    return f"cooldown after SL {age_min:.0f}/{cooldown_min}m"

    max_consecutive_sl = _setting_int("symbol_max_consecutive_sl", 3)
    if max_consecutive_sl > 0:
        rows = conn.execute(
            """
            SELECT close_reason, reason_close, closed_at
              FROM trades
             WHERE symbol=? AND status!='OPEN'
             ORDER BY id DESC
             LIMIT ?
            """,
            (symbol, max_consecutive_sl),
        ).fetchall()
        if len(rows) >= max_consecutive_sl:
            reasons = [
                str(_row_value(row, "close_reason", 0) or _row_value(row, "reason_close", 1) or "").upper()
                for row in rows
            ]
            if all(reason == "SL" for reason in reasons):
                streak_cooldown_min = _setting_int("symbol_consecutive_sl_cooldown_min", 360)
                if streak_cooldown_min <= 0:
                    return None
                closed_ts = _parse_closed_ts(_row_value(rows[0], "closed_at", 2))
                if closed_ts is None:
                    return f"{max_consecutive_sl} consecutive SL"
                age_min = (time.time() - closed_ts) / 60.0
                if age_min < streak_cooldown_min:
                    return (
                        f"{max_consecutive_sl} consecutive SL "
                        f"cooldown {age_min:.0f}/{streak_cooldown_min}m"
                    )

    min_trades = _setting_int("symbol_ban_min_trades", 4)
    lookback = _setting_int("symbol_ban_lookback", 12)
    max_pnl = _setting_float("symbol_ban_max_pnl", -2.0)
    if min_trades > 0 and lookback >= min_trades:
        rows = conn.execute(
            """
            SELECT pnl_usd, pnl, closed_at
              FROM trades
             WHERE symbol=? AND status!='OPEN'
             ORDER BY id DESC
             LIMIT ?
            """,
            (symbol, lookback),
        ).fetchall()
        if len(rows) >= min_trades:
            wins = sum(1 for row in rows if _trade_pnl(row) > 0)
            total_pnl = sum(_trade_pnl(row) for row in rows)
            if wins == 0 and total_pnl <= max_pnl:
                ban_cooldown_min = _setting_int("symbol_ban_cooldown_min", 720)
                if ban_cooldown_min <= 0:
                    return None
                closed_ts = _parse_closed_ts(_row_value(rows[0], "closed_at", 2))
                if closed_ts is None:
                    return f"symbol ban {wins}/{len(rows)} wins pnl={total_pnl:.2f}"
                age_min = (time.time() - closed_ts) / 60.0
                if age_min < ban_cooldown_min:
                    return (
                        f"symbol ban {wins}/{len(rows)} wins pnl={total_pnl:.2f} "
                        f"cooldown {age_min:.0f}/{ban_cooldown_min}m"
                    )
    return None


def _direction_edge_block_reason(conn, direction: str, trade_mode: str) -> Optional[str]:
    if not _setting_bool("direction_edge_guard_enabled", True):
        return None
    direction = str(direction or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return None
    lookback = _setting_int("direction_edge_guard_lookback", 6)
    min_trades = _setting_int("direction_edge_guard_min_trades", 4)
    min_wr = _setting_float("direction_edge_guard_min_wr", 0.25)
    min_sum_r = _setting_float("direction_edge_guard_min_sum_r", 0.0)
    if lookback <= 0 or min_trades <= 0:
        return None

    epoch_ts = _perf_epoch_ts()
    epoch_filter = ""
    params: List[Any] = [direction, str(trade_mode or "standard").lower()]
    if epoch_ts:
        epoch_filter = """
           AND (
                (closed_at GLOB '[0-9]*' AND CAST(closed_at AS INTEGER) >= ?)
                OR closed_at >= datetime(?, 'unixepoch')
           )
        """
        params.extend([epoch_ts, epoch_ts])
    
    params.append(lookback)
    rows = conn.execute(
        f"""
        SELECT COALESCE(rr_realized, rr, 0) AS r
          FROM trades
         WHERE status!='OPEN'
           AND UPPER(direction)=?
           AND LOWER(COALESCE(trade_mode, 'standard'))=?
           {epoch_filter}
         ORDER BY id DESC
         LIMIT ?
        """,
        tuple(params),
    ).fetchall()
    if len(rows) < min_trades and epoch_ts:
        log.info("[autopost] direction_edge warmup %s/%s: closed %d/%d since reset",
                 trade_mode, direction, len(rows), min_trades)
        return None
    if len(rows) < min_trades:
        return None

    values: List[float] = []
    for row in rows:
        try:
            values.append(float(_row_value(row, "r", 0) or 0.0))
        except Exception:
            values.append(0.0)
    if len(values) < min_trades:
        return None

    wins = sum(1 for value in values if value > 0)
    wr = wins / len(values)
    sum_r = sum(values)
    if wr < min_wr or sum_r < min_sum_r:
        return (
            f"direction_edge {direction} last {wins}/{len(values)} WR={wr:.0%} "
            f"sumR={sum_r:+.2f} need WR>={min_wr:.0%} sumR>={min_sum_r:+.2f}"
        )
    return None


def _risk_decision_code(reason: str) -> str:
    text = str(reason).lower()
    if "cooldown" in text:
        return "SYMBOL_COOLDOWN"
    if "ban" in text or "consecutive sl" in text:
        return "SYMBOL_BAN"
    return "SYMBOL_RISK_BLOCK"


def _long_quality_block_reason(c: Dict[str, Any]) -> Optional[str]:
    if not _setting_bool("long_only_quality_enabled", True):
        return None
    direction = str(c.get("direction", "LONG")).upper()
    if direction != "LONG" or not _setting_bool("autopost_disable_shorts", False):
        return None

    ind = c.get("ind") or {}
    checks: List[str] = []
    min_adx = _setting_float("long_only_min_adx", 22.0)
    min_vol = _setting_float("long_only_min_vol_ratio", 0.90)
    max_vwap = _setting_float("long_only_max_vwap_delta_pct", 0.30)
    max_bb = _setting_float("long_only_max_bb_pct_b", 0.80)

    adx = ind.get("adx14")
    if adx is not None:
        try:
            if float(adx) < min_adx:
                checks.append(f"ADX {float(adx):.1f} < {min_adx:.1f}")
        except Exception:
            pass

    vol_ratio = ind.get("vol_ratio")
    if vol_ratio is not None:
        try:
            if float(vol_ratio) < min_vol:
                checks.append(f"vol_ratio {float(vol_ratio):.2f} < {min_vol:.2f}")
        except Exception:
            pass

    vwap_delta = ind.get("vwap_delta_pct")
    if vwap_delta is not None:
        try:
            if float(vwap_delta) > max_vwap:
                checks.append(f"VWAP delta {float(vwap_delta):+.2f}% > {max_vwap:.2f}%")
        except Exception:
            pass

    bb_pct_b = ind.get("bb_pct_b")
    if bb_pct_b is not None:
        try:
            if float(bb_pct_b) > max_bb:
                checks.append(f"BB%B {float(bb_pct_b):.2f} > {max_bb:.2f}")
        except Exception:
            pass

    return "; ".join(checks) if checks else None


def _market_regime(c: Dict[str, Any]) -> str:
    ind = c.get("ind") or {}
    try:
        vol_ratio = float(ind.get("vol_ratio") or 0.0)
    except Exception:
        vol_ratio = 0.0
    try:
        adx = float(ind.get("adx14") or 0.0)
    except Exception:
        adx = 0.0
    try:
        ema50 = float(ind.get("ema50"))
        ema200 = float(ind.get("ema200"))
    except Exception:
        ema50 = ema200 = None

    low_liq_vol = _setting_float("regime_low_liquidity_vol_ratio", 0.50)
    chop_adx = _setting_float("regime_chop_adx", 16.0)
    trend_adx = _setting_float("regime_trend_adx", 22.0)

    if vol_ratio and vol_ratio < low_liq_vol:
        return "LOW_LIQUIDITY"
    if adx and adx < chop_adx:
        return "CHOP"
    if ema50 is not None and ema200 is not None and adx >= trend_adx:
        return "TREND_UP" if ema50 >= ema200 else "TREND_DOWN"
    return "RANGE"


def _regime_block_reason(c: Dict[str, Any]) -> Optional[str]:
    if not _setting_bool("market_regime_filter_enabled", True):
        return None
    direction = str(c.get("direction", "LONG")).upper()
    regime = _market_regime(c)
    if direction == "LONG":
        allowed = _parse_csv_set(get_setting("regime_long_allowed", "TREND_UP"))
        if allowed and regime not in allowed:
            return f"regime {regime} not in {sorted(allowed)}"
    if direction == "SHORT":
        allowed = _parse_csv_set(get_setting("regime_short_allowed", "TREND_DOWN"))
        if allowed and regime not in allowed:
            return f"regime {regime} not in {sorted(allowed)}"
    return None


def _ev_block_reason(conn, symbol: str, direction: str, trade_mode: str) -> Optional[str]:
    if not _setting_bool("ev_filter_enabled", True):
        return None
    min_trades = _setting_int("ev_min_trades", 8)
    lookback = _setting_int("ev_lookback", 50)
    min_ev = _setting_float("ev_min_r", 0.0)
    if min_trades <= 0 or lookback <= 0:
        return None

    rows = conn.execute(
        """
        SELECT rr_realized
          FROM trades
         WHERE symbol=?
           AND UPPER(direction)=?
           AND LOWER(COALESCE(trade_mode, 'standard'))=?
           AND status!='OPEN'
           AND rr_realized IS NOT NULL
         ORDER BY id DESC
         LIMIT ?
        """,
        (symbol, direction.upper(), str(trade_mode or "standard").lower(), lookback),
    ).fetchall()
    if len(rows) < min_trades:
        return None

    values: List[float] = []
    for row in rows:
        try:
            values.append(float(_row_value(row, "rr_realized", 0)))
        except Exception:
            continue
    if len(values) < min_trades:
        return None

    wins = [v for v in values if v > 0]
    losses = [abs(v) for v in values if v <= 0]
    if not wins or not losses:
        return None

    wr = len(wins) / len(values)
    avg_win = sum(wins) / len(wins)
    avg_loss = sum(losses) / len(losses)
    ev = (wr * avg_win) - ((1.0 - wr) * avg_loss)
    if ev <= min_ev:
        return (
            f"EV {ev:+.2f}R <= {min_ev:+.2f}R "
            f"({len(wins)}/{len(values)} WR={wr:.0%}, win={avg_win:.2f}R, loss={avg_loss:.2f}R)"
        )
    return None


def _record_candidate_paper(
    conn,
    c: Dict[str, Any],
    *,
    source: str,
    trade_mode: str,
    reason: str,
) -> Optional[int]:
    try:
        symbol = str(c["symbol"]).upper()
        timeframe = str(c.get("timeframe", "1h")).lower()
        direction = str(c.get("direction", "LONG")).upper()
        return record_paper_signal(
            source=source,
            symbol=symbol,
            timeframe=timeframe,
            direction=direction,
            trade_mode=trade_mode,
            entry=c.get("entry"),
            sl=c.get("sl"),
            tp=c.get("tp"),
            rr=c.get("rr_adj") or c.get("rr_target") or c.get("rr"),
            reason=reason,
            indicators=c.get("ind") or c,
            conn=conn,
        )
    except Exception as exc:
        log.debug("[autopost] paper record failed: %s", exc)
        return None


def _near_miss_gate_reason(c: Dict[str, Any], effective_gate_pct: float) -> Optional[str]:
    if not _setting_bool("near_miss_paper_enabled", True):
        return None
    gate_score = c.get("gate_score")
    gate_total = c.get("gate_total")
    if gate_score is None or gate_total is None:
        return None
    try:
        score = float(gate_score)
        total = float(gate_total)
        threshold = total * (float(effective_gate_pct) / 100.0)
    except (TypeError, ValueError):
        return None
    gap = threshold - score
    if gap <= 0:
        return None
    max_gap = _setting_float("near_miss_gate_gap_points", 1.0)
    if gap > max_gap:
        return None

    hard_blockers = c.get("hard_blockers") or (c.get("ind") or {}).get("hard_blockers") or []
    if hard_blockers and not _setting_bool("near_miss_allow_hard_blockers", True):
        return None
    gate_pct = (score / total * 100.0) if total > 0 else 0.0
    return (
        f"near_miss gate={gate_score}/{gate_total} "
        f"({gate_pct:.1f}%) need>={effective_gate_pct:.0f}% gap={gap:.2f}"
    )


def _current_local_hour() -> int:
    tz_name = os.getenv("TZ_NAME") or "Europe/Kyiv"
    try:
        return datetime.now(ZoneInfo(tz_name)).hour
    except Exception:
        return datetime.now().hour


def _load_profit_guard(us: Dict[str, Any], trade_mode: str) -> Dict[str, Any]:
    scalping_defaults = str(trade_mode).lower() == "scalping"
    standard_defaults = not scalping_defaults
    enabled_raw = us.get("profit_guard_enabled")
    if enabled_raw in (None, ""):
        env_default = "true" if scalping_defaults else "false"
        enabled_raw = get_setting("profit_guard_enabled", env_default)
    if standard_defaults and str(enabled_raw).lower() in ("1", "true", "yes", "on"):
        standard_override = us.get("profit_guard_standard_enabled")
        if standard_override in (None, ""):
            standard_override = get_setting("profit_guard_standard_enabled", "false")
        enabled_raw = standard_override

    blocked_symbols_raw = us.get("profit_guard_blocked_symbols")
    if blocked_symbols_raw in (None, ""):
        blocked_symbols_raw = get_setting("profit_guard_blocked_symbols", "ETHUSDT,BNBUSDT,LTCUSDT")

    allowed_hours_raw = us.get("profit_guard_hours")
    if allowed_hours_raw in (None, ""):
        allowed_hours_raw = get_setting("profit_guard_hours", "13,14,15,16")

    min_gate_pct_raw = us.get("profit_guard_min_gate_pct")
    if min_gate_pct_raw in (None, ""):
        min_gate_pct_raw = get_setting("profit_guard_min_gate_pct", "72")

    short_gate_bonus_raw = us.get("profit_guard_short_extra_gate_pct")
    if short_gate_bonus_raw in (None, ""):
        short_gate_bonus_raw = get_setting("profit_guard_short_extra_gate_pct", "5")

    short_rr_bonus_raw = us.get("profit_guard_short_rr_bonus")
    if short_rr_bonus_raw in (None, ""):
        short_rr_bonus_raw = get_setting("profit_guard_short_rr_bonus", "0.10")

    min_adx_raw = us.get("profit_guard_min_adx")
    if min_adx_raw in (None, ""):
        min_adx_raw = get_setting("profit_guard_min_adx", "22")

    min_vol_ratio_raw = us.get("profit_guard_min_vol_ratio")
    if min_vol_ratio_raw in (None, ""):
        min_vol_ratio_raw = get_setting("profit_guard_min_vol_ratio", "0.9")

    return {
        "enabled": str(enabled_raw).lower() in ("1", "true", "yes", "on"),
        "blocked_symbols": _parse_csv_set(blocked_symbols_raw),
        "allowed_hours": _parse_hour_set(allowed_hours_raw),
        "min_gate_pct": float(min_gate_pct_raw or 72.0),
        "short_extra_gate_pct": float(short_gate_bonus_raw or 5.0),
        "short_rr_bonus": float(short_rr_bonus_raw or 0.10),
        "min_adx": float(min_adx_raw or 22.0),
        "min_vol_ratio": float(min_vol_ratio_raw or 0.9),
    }


def _gate_ok(c: Dict[str, Any], rr_t: Optional[float], quality_gate_pct: float = 70.0) -> Tuple[bool, str]:
    """Check if candidate passes gate.
    
    Args:
        c: Candidate dict with gate_score, gate_total etc.
        rr_t: RR threshold
        quality_gate_pct: Minimum gate percentage (0-100), default 50%
    """
    gate_score = c.get("gate_score")
    gate_total = c.get("gate_total")
    if gate_score is not None and gate_total is not None:
        gate_threshold = gate_total * (quality_gate_pct / 100.0)
        if gate_score < gate_threshold:
            return False, f"gate_score={gate_score}/{gate_total} (<{quality_gate_pct:.0f}%)"
    
    # For scalping, use rr_adj or rr_target from candidate
    effective_rr = c.get("rr_target") or c.get("rr_adj") or rr_t
    if effective_rr is None or effective_rr < 1.0:
        return False, f"rr_target={effective_rr}"
    return True, "ok"


def _skip_context(c: Dict[str, Any], effective_gate_pct: Optional[float] = None) -> str:
    ind = c.get("ind") or {}
    gate_score = c.get("gate_score")
    gate_total = c.get("gate_total")
    bits: List[str] = []
    if gate_score is not None and gate_total is not None:
        gate_part = f"gate={gate_score}/{gate_total}"
        if effective_gate_pct is not None:
            gate_part += f" need>={effective_gate_pct:.0f}%"
        bits.append(gate_part)
    hard_blockers = c.get("hard_blockers") or ind.get("hard_blockers") or []
    if hard_blockers:
        bits.append("hard=" + "; ".join(str(x) for x in hard_blockers[:3]))
    for label, key, fmt in (
        ("RSI", "rsi14", "{:.1f}"),
        ("Stoch", "stoch_k", "K:{:.1f}"),
        ("ADX", "adx14", "{:.1f}"),
        ("Vol", "vol_ratio", "{:.2f}x"),
        ("VWAPΔ", "vwap_delta_pct", "{:+.2f}%"),
        ("BB%B", "bb_pct_b", "{:.2f}"),
    ):
        val = ind.get(key)
        if val is None:
            continue
        try:
            bits.append(f"{label}={fmt.format(float(val))}")
        except Exception:
            continue
    return " | ".join(bits)


def _parse_rr_from_reasons(reasons: Optional[List[str]]) -> Optional[float]:
    """Parse RR from reasons list."""
    if not reasons:
        return None
    for r in reasons:
        if "RR=" in str(r):
            try:
                return float(r.split("RR=")[-1])
            except Exception:
                pass
    return None


def _qscore_basic(direction: str, rr_est: Optional[float], df) -> Tuple[int, List[str]]:
    """Basic quality score."""
    return quick_qscore(direction, rr_est, df)


async def run_autopost_once(application=None) -> List[Dict[str, Any]]:
    """Main autopost execution function."""
    from utils.user_settings import get_user_settings
    
    default_chat = os.getenv("TELEGRAM_CHAT_ID", "1126438536")
    user_id = os.getenv("TELEGRAM_CHAT_ID", "1126438536")
    
    # Get user settings for scalping mode
    us = get_user_settings(user_id) if user_id else {}
    scalping_mode = int(us.get("scalping_mode") or 0) if isinstance(us, dict) else 0
    
    # Choose signal source based on mode
    if scalping_mode:
        try:
            from services.scalping_sources import collect_scalping_candidates
            sl_pct = float(us.get("scalping_sl_pct") or 0.3) if isinstance(us, dict) else 0.3
            tp_pct = float(us.get("scalping_tp_pct") or 0.9) if isinstance(us, dict) else 0.9
            slippage_pct = float(us.get("slippage_pct") or 0.05) if isinstance(us, dict) else 0.05
            candidates = await collect_scalping_candidates(
                sl_pct=sl_pct,
                tp_pct=tp_pct,
                slippage_pct=slippage_pct,
                timeframe="5m",  # Скальпінг на 5m
            )
            trade_mode = "scalping"
            log.info(f"[autopost] SCALPING MODE: SL={sl_pct}% TP={tp_pct}% Slip={slippage_pct}%")
        except Exception as e:
            log.warning(f"[autopost] scalping_sources failed: {e}, fallback to standard")
            from services.autopost_sources import collect_autopost_candidates
            candidates = collect_autopost_candidates()
            trade_mode = "standard"
    else:
        try:
            from services.autopost_sources import collect_autopost_candidates
            candidates = collect_autopost_candidates()
            trade_mode = "standard"
        except Exception:
            log.info("[autopost] no autopost_sources.collect_autopost_candidates(), nothing to send")
            return []
    
    if not candidates:
        return []

    with get_conn() as conn:
        block_reason = _global_low_wr_block_reason(conn)
        if block_reason:
            log_decision(
                source="autopost",
                decision="PAUSED",
                reason=block_reason,
                trade_mode=trade_mode,
                risk_state="PAUSED",
                conn=conn,
            )
            paper_count = 0
            if _setting_bool("paper_capture_while_paused", True):
                for c in candidates:
                    if _record_candidate_paper(
                        conn,
                        c,
                        source="autopost_paused",
                        trade_mode=trade_mode,
                        reason=block_reason,
                    ):
                        paper_count += 1
                log.info("[autopost] PAUSED paper captured %d candidate(s)", paper_count)
    if block_reason:
        log.info("[autopost] PAUSED: %s", block_reason)
        return []
    
    dedup_sec = int(get_setting("dedup_window_sec", "90") or 90)
    
    preset = (get_setting("indicator_preset", os.getenv("INDICATOR_PRESET", "")) or "").lower()
    want_panel = (preset == "preset3") or (str(get_setting("autopost_panel_verbose", "false")).lower() == "true")
    
    quality_on = str(get_setting("quality_select_enabled", "false")).lower() == "true"
    quality_min = float(get_setting("quality_min", "50") or 50.0)
    quality_topk = int(get_setting("quality_top_k", "3") or 3)
    
    prepared: List[Dict[str, Any]] = []
    seen_keys: Set[Tuple[str, str]] = set()
    
    with get_conn() as conn:
        for c in candidates:
            try:
                symbol = str(c["symbol"]).upper()
                direction = str(c.get("direction", "LONG")).upper()
                timeframe = str(c.get("timeframe", "1h")).lower()
                short_open_blocked = direction == "SHORT" and _setting_bool("autopost_disable_shorts", False)
                signal_only = short_open_blocked and _setting_bool("short_signal_only", True)
                risk_reason = _symbol_risk_block_reason(conn, symbol)
                if risk_reason:
                    if signal_only:
                        c.setdefault("signal_only_warnings", []).append(risk_reason)
                        log.info("[autopost] SIGNAL_ONLY %s/%s with risk warning: %s", symbol, timeframe, risk_reason)
                        log_decision(
                            source="autopost",
                            decision="SHORT_SIGNAL_ONLY_RISK",
                            reason=risk_reason,
                            candidate=c,
                            symbol=symbol,
                            timeframe=timeframe,
                            direction=direction,
                            trade_mode=trade_mode,
                            risk_state="SIGNAL_ONLY",
                            conn=conn,
                        )
                    else:
                        log.info("[autopost] SKIP %s/%s: %s", symbol, timeframe, risk_reason)
                        log_decision(
                            source="autopost",
                            decision=_risk_decision_code(risk_reason),
                            reason=risk_reason,
                            candidate=c,
                            symbol=symbol,
                            timeframe=timeframe,
                            direction=direction,
                            trade_mode=trade_mode,
                            risk_state="SYMBOL_RISK",
                            conn=conn,
                        )
                        continue
                direction_edge_reason = _direction_edge_block_reason(conn, direction, trade_mode)
                if direction_edge_reason:
                    _record_candidate_paper(
                        conn,
                        c,
                        source="autopost_direction_edge_guard",
                        trade_mode=trade_mode,
                        reason=direction_edge_reason,
                    )
                    log.info("[autopost] SKIP %s/%s: %s", symbol, timeframe, direction_edge_reason)
                    log_decision(
                        source="autopost",
                        decision="DIRECTION_EDGE_GUARD",
                        reason=direction_edge_reason,
                        candidate=c,
                        symbol=symbol,
                        timeframe=timeframe,
                        direction=direction,
                        trade_mode=trade_mode,
                        risk_state="DIRECTION_EDGE",
                        conn=conn,
                    )
                    continue
                entry = float(c["entry"])
                sl = float(c["sl"])
                tp = c.get("tp")
                tp = float(tp) if tp is not None else None
                chat_id = c.get("chat_id") or default_chat
                if not chat_id:
                    log.warning("[autopost] skip %s/%s: chat_id is empty", symbol, timeframe)
                    log_decision(
                        source="autopost",
                        decision="SKIP",
                        reason="chat_id_empty",
                        candidate=c,
                        symbol=symbol,
                        timeframe=timeframe,
                        direction=direction,
                        trade_mode=trade_mode,
                        conn=conn,
                    )
                    continue
                
                key = (symbol, timeframe)
                if key in seen_keys:
                    log.info("[autopost] in-run dedup %s/%s — skip", symbol, timeframe)
                    log_decision(
                        source="autopost",
                        decision="SKIP",
                        reason="in_run_dedup",
                        candidate=c,
                        symbol=symbol,
                        timeframe=timeframe,
                        direction=direction,
                        trade_mode=trade_mode,
                        conn=conn,
                    )
                    continue
                seen_keys.add(key)
                
                if seen_recently(conn, user_id, symbol, timeframe, window_sec=dedup_sec):
                    log.info("[autopost] dedup_recent %s/%s — skip", symbol, timeframe)
                    log_decision(
                        source="autopost",
                        decision="SKIP",
                        reason="dedup_recent",
                        candidate=c,
                        symbol=symbol,
                        timeframe=timeframe,
                        direction=direction,
                        trade_mode=trade_mode,
                        conn=conn,
                    )
                    continue

                # Get user settings early for quality gate and performance guard
                us = get_user_settings(user_id) if user_id else {}
                quality_gate_pct = float(us.get("quality_gate_pct") or 70) if isinstance(us, dict) else 70.0
                profit_guard = _load_profit_guard(us if isinstance(us, dict) else {}, trade_mode)
                short_open_blocked = direction == "SHORT" and _setting_bool("autopost_disable_shorts", False)
                signal_only = short_open_blocked and _setting_bool("short_signal_only", True)
                local_hour = _current_local_hour()
                user_symbols = _parse_csv_set((us or {}).get("monitored_symbols")) if isinstance(us, dict) else set()

                if profit_guard["enabled"]:
                    # Respect explicit user choice from /panel:
                    # if user manually selected symbols, do not hard-block them here.
                    if symbol in profit_guard["blocked_symbols"] and symbol not in user_symbols:
                        if signal_only:
                            c.setdefault("signal_only_warnings", []).append("profit_guard blocked symbol")
                        else:
                            log.info("[autopost] SKIP %s/%s: profit_guard blocked symbol", symbol, timeframe)
                            log_decision(
                                source="autopost",
                                decision="PROFIT_GUARD",
                                reason="profit_guard blocked symbol",
                                candidate=c,
                                symbol=symbol,
                                timeframe=timeframe,
                                direction=direction,
                                trade_mode=trade_mode,
                                risk_state="PROFIT_GUARD",
                                conn=conn,
                            )
                            continue
                    if profit_guard["allowed_hours"] and local_hour not in profit_guard["allowed_hours"]:
                        reason = f"profit_guard hour {local_hour:02d} outside {sorted(profit_guard['allowed_hours'])}"
                        if signal_only:
                            c.setdefault("signal_only_warnings", []).append(reason)
                        else:
                            log.info(
                                "[autopost] SKIP %s/%s: profit_guard hour %02d outside %s",
                                symbol, timeframe, local_hour, sorted(profit_guard["allowed_hours"])
                            )
                            log_decision(
                                source="autopost",
                                decision="PROFIT_GUARD",
                                reason=reason,
                                candidate=c,
                                symbol=symbol,
                                timeframe=timeframe,
                                direction=direction,
                                trade_mode=trade_mode,
                                risk_state="PROFIT_GUARD",
                                conn=conn,
                            )
                            continue

                effective_gate_pct = quality_gate_pct
                if profit_guard["enabled"] and trade_mode == "scalping" and not signal_only:
                    effective_gate_pct = max(effective_gate_pct, profit_guard["min_gate_pct"])
                    if direction == "SHORT" and not signal_only:
                        effective_gate_pct += profit_guard["short_extra_gate_pct"]
                    elif c.get("long_momentum_mode"):
                        try:
                            momentum_gate_pct = float(c.get("long_momentum_gate_pct") or effective_gate_pct)
                            effective_gate_pct = min(effective_gate_pct, momentum_gate_pct)
                        except Exception:
                            pass

                rr_m = compute_rr_metrics(entry, sl, tp)
                rr_t = rr_m.get("rr_target")
                ok, reason = _gate_ok(c, rr_t, effective_gate_pct)
                if not ok:
                    skip_context = _skip_context(c, effective_gate_pct)
                    near_miss_reason = _near_miss_gate_reason(c, effective_gate_pct)
                    if near_miss_reason:
                        paper_id = _record_candidate_paper(
                            conn,
                            c,
                            source="autopost_near_miss_gate",
                            trade_mode=trade_mode,
                            reason=near_miss_reason,
                        )
                        if paper_id:
                            log.info(
                                "[autopost] PAPER_NEAR_MISS %s/%s: %s paper#%s",
                                symbol, timeframe, near_miss_reason, paper_id
                            )
                            log_decision(
                                source="autopost",
                                decision="PAPER_NEAR_MISS",
                                reason=near_miss_reason,
                                candidate=c,
                                symbol=symbol,
                                timeframe=timeframe,
                                direction=direction,
                                trade_mode=trade_mode,
                                rr=rr_t,
                                risk_state="PAPER",
                                conn=conn,
                            )
                    log.info(
                        "[autopost] SKIP %s/%s: %s | %s",
                        symbol, timeframe, reason, skip_context
                    )
                    log_decision(
                        source="autopost",
                        decision="GATE_FAIL",
                        reason=f"{reason} | {skip_context}" if skip_context else reason,
                        candidate=c,
                        symbol=symbol,
                        timeframe=timeframe,
                        direction=direction,
                        trade_mode=trade_mode,
                        rr=rr_t,
                        risk_state="GATE_FAIL",
                        conn=conn,
                    )
                    # reset stable-pass counter on explicit gate fail
                    try:
                        from services.autopost.persistence import reset_candidate_pass
                        reset_candidate_pass(user_id=str(user_id), symbol=symbol, timeframe=timeframe)
                    except Exception:
                        pass
                    try:
                        from services.metrics import inc_skip
                        inc_skip()
                    except Exception:
                        pass
                    continue

                # Stable-checks: require N consecutive passes before sending/opening
                stable_checks = int(us.get("stable_checks") or int(get_setting("autopost_stable_checks", "1") or 1))
                try:
                    from services.autopost.persistence import record_candidate_pass, get_candidate_passes
                    passes = record_candidate_pass(user_id=str(user_id), symbol=symbol, timeframe=timeframe)
                    if passes < stable_checks:
                        log.info("[autopost] UNSTABLE %s/%s: pass %d/%d — wait next run", symbol, timeframe, passes, stable_checks)
                        log_decision(
                            source="autopost",
                            decision="SKIP",
                            reason=f"unstable pass {passes}/{stable_checks}",
                            candidate=c,
                            symbol=symbol,
                            timeframe=timeframe,
                            direction=direction,
                            trade_mode=trade_mode,
                            rr=rr_t,
                            risk_state="UNSTABLE",
                            conn=conn,
                        )
                        # don't prepare message until stable
                        continue
                except Exception:
                    # If persistence fails, fall back to immediate behavior (do not block autopost)
                    log.debug("[autopost] candidate persistence failed for %s/%s — proceeding", symbol, timeframe)
                
                # Use rr_adj from scalping_sources if available (more accurate with slippage)
                rr_num = c.get("rr_adj") or c.get("rr_target") or compute_rr_num(
                    direction,
                    safe_float(entry) if safe_float(entry) is not None else math.nan,
                    safe_float(sl) if safe_float(sl) is not None else math.nan,
                    safe_float(tp) if safe_float(tp) is not None else math.nan,
                )
                
                # For scalping mode, calculate RR threshold from TP/SL % settings
                # This ensures threshold matches actual calculated RR
                if trade_mode == "scalping":
                    scalping_tp = float(us.get("scalping_tp_pct") or 1.2) if isinstance(us, dict) else 1.2
                    scalping_sl = float(us.get("scalping_sl_pct") or 0.3) if isinstance(us, dict) else 0.3
                    slippage = float(us.get("slippage_pct") or 0.08) if isinstance(us, dict) else 0.08
                    # Calculate expected RR after slippage (same as scalping_sources)
                    # For LONG: Risk = SL% + 2*slip%, Reward = TP% - 2*slip%
                    # Because slippage hits BOTH entry (worse) and exit (worse)
                    effective_reward = scalping_tp - 2 * slippage
                    effective_risk = scalping_sl + 2 * slippage
                    expected_rr_with_slip = effective_reward / effective_risk if effective_risk > 0 else 2.0
                    # Use 95% of expected RR as threshold to account for rounding
                    rr_min = expected_rr_with_slip * 0.95
                    log.debug("[autopost] SCALP RR threshold: %.2f (from TP=%.2f%% SL=%.2f%% slip=%.2f%% -> reward=%.2f%% risk=%.2f%%)",
                             rr_min, scalping_tp, scalping_sl, slippage, effective_reward, effective_risk)
                else:
                    rr_min = float(
                        (us.get("autopost_rr") if isinstance(us, dict) else None)
                        or (us.get("rr_threshold") if isinstance(us, dict) else None)
                        or CFG.get("rr_threshold", 1.5)
                    )
                
                # Get indicator summary for detailed logging
                ind_data = c.get("ind") or {}
                gate_score_val = c.get("gate_score")
                gate_total_val = c.get("gate_total")
                hard_blockers = c.get("hard_blockers") or ind_data.get("hard_blockers") or []

                if hard_blockers:
                    if signal_only:
                        log.info(
                            "[autopost] SIGNAL_ONLY %s/%s with blockers: %s",
                            symbol, timeframe, "; ".join(str(x) for x in hard_blockers)
                        )
                        log_decision(
                            source="autopost",
                            decision="SHORT_SIGNAL_ONLY_BLOCKERS",
                            reason="; ".join(str(x) for x in hard_blockers),
                            candidate=c,
                            symbol=symbol,
                            timeframe=timeframe,
                            direction=direction,
                            trade_mode=trade_mode,
                            rr=rr_num,
                            risk_state="SIGNAL_ONLY",
                            conn=conn,
                        )
                    else:
                        log.info(
                            "[autopost] SKIP %s/%s: hard_blockers=%s",
                            symbol, timeframe, "; ".join(str(x) for x in hard_blockers)
                        )
                        log_decision(
                            source="autopost",
                            decision="HARD_BLOCKERS",
                            reason="; ".join(str(x) for x in hard_blockers),
                            candidate=c,
                            symbol=symbol,
                            timeframe=timeframe,
                            direction=direction,
                            trade_mode=trade_mode,
                            rr=rr_num,
                            risk_state="HARD_BLOCKERS",
                            conn=conn,
                        )
                        try:
                            from services.autopost.persistence import reset_candidate_pass
                            reset_candidate_pass(user_id=str(user_id), symbol=symbol, timeframe=timeframe)
                        except Exception:
                            pass
                        continue

                long_quality_reason = _long_quality_block_reason(c)
                if long_quality_reason:
                    log.info(
                        "[autopost] SKIP %s/%s: long_only_quality %s",
                        symbol, timeframe, long_quality_reason
                    )
                    log_decision(
                        source="autopost",
                        decision="LONG_QUALITY",
                        reason=long_quality_reason,
                        candidate=c,
                        symbol=symbol,
                        timeframe=timeframe,
                        direction=direction,
                        trade_mode=trade_mode,
                        rr=rr_num,
                        risk_state="LONG_QUALITY",
                        conn=conn,
                    )
                    try:
                        from services.autopost.persistence import reset_candidate_pass
                        reset_candidate_pass(user_id=str(user_id), symbol=symbol, timeframe=timeframe)
                    except Exception:
                        pass
                    continue

                regime = _market_regime(c)
                regime_reason = _regime_block_reason(c)
                if regime_reason:
                    if signal_only:
                        c.setdefault("signal_only_warnings", []).append(regime_reason)
                        log.info("[autopost] SIGNAL_ONLY %s/%s with regime warning: %s", symbol, timeframe, regime_reason)
                    else:
                        log.info(
                            "[autopost] SKIP %s/%s: %s",
                            symbol, timeframe, regime_reason
                        )
                        log_decision(
                            source="autopost",
                            decision="REGIME_FILTER",
                            reason=regime_reason,
                            candidate=c,
                            symbol=symbol,
                            timeframe=timeframe,
                            direction=direction,
                            trade_mode=trade_mode,
                            rr=rr_num,
                            risk_state=regime,
                            conn=conn,
                        )
                        try:
                            from services.autopost.persistence import reset_candidate_pass
                            reset_candidate_pass(user_id=str(user_id), symbol=symbol, timeframe=timeframe)
                        except Exception:
                            pass
                        continue

                ev_reason = _ev_block_reason(conn, symbol, direction, trade_mode)
                if ev_reason:
                    if signal_only:
                        c.setdefault("signal_only_warnings", []).append(ev_reason)
                        log.info("[autopost] SIGNAL_ONLY %s/%s with EV warning: %s", symbol, timeframe, ev_reason)
                    else:
                        log.info("[autopost] SKIP %s/%s: %s", symbol, timeframe, ev_reason)
                        log_decision(
                            source="autopost",
                            decision="EV_FAIL",
                            reason=ev_reason,
                            candidate=c,
                            symbol=symbol,
                            timeframe=timeframe,
                            direction=direction,
                            trade_mode=trade_mode,
                            rr=rr_num,
                            risk_state="EV_FAIL",
                            conn=conn,
                        )
                        try:
                            from services.autopost.persistence import reset_candidate_pass
                            reset_candidate_pass(user_id=str(user_id), symbol=symbol, timeframe=timeframe)
                        except Exception:
                            pass
                        continue

                if profit_guard["enabled"] and trade_mode == "scalping" and not signal_only:
                    adx_val = float(ind_data.get("adx14") or 0.0)
                    vol_ratio_val = float(ind_data.get("vol_ratio") or 0.0)
                    if adx_val < profit_guard["min_adx"]:
                        log.info(
                            "[autopost] SKIP %s/%s: profit_guard ADX %.1f < %.1f",
                            symbol, timeframe, adx_val, profit_guard["min_adx"]
                        )
                        log_decision(
                            source="autopost",
                            decision="PROFIT_GUARD",
                            reason=f"profit_guard ADX {adx_val:.1f} < {profit_guard['min_adx']:.1f}",
                            candidate=c,
                            symbol=symbol,
                            timeframe=timeframe,
                            direction=direction,
                            trade_mode=trade_mode,
                            rr=rr_num,
                            risk_state="PROFIT_GUARD",
                            conn=conn,
                        )
                        continue
                    if vol_ratio_val < profit_guard["min_vol_ratio"]:
                        log.info(
                            "[autopost] SKIP %s/%s: profit_guard volume %.2f < %.2f",
                            symbol, timeframe, vol_ratio_val, profit_guard["min_vol_ratio"]
                        )
                        log_decision(
                            source="autopost",
                            decision="PROFIT_GUARD",
                            reason=f"profit_guard volume {vol_ratio_val:.2f} < {profit_guard['min_vol_ratio']:.2f}",
                            candidate=c,
                            symbol=symbol,
                            timeframe=timeframe,
                            direction=direction,
                            trade_mode=trade_mode,
                            rr=rr_num,
                            risk_state="PROFIT_GUARD",
                            conn=conn,
                        )
                        continue
                
                if (rr_num is None) or (rr_num < rr_min):
                    # Detailed skip log with indicators
                    log.info("[autopost] SKIP %s/%s: RR=%.2f<%.2f gate=%s/%s RSI=%.1f ADX=%.1f",
                             symbol, timeframe, 
                             rr_num if rr_num else 0, rr_min,
                             gate_score_val, gate_total_val,
                             ind_data.get("rsi14", 0), ind_data.get("adx14", 0))
                    log_decision(
                        source="autopost",
                        decision="RR_FAIL",
                        reason=f"RR={(rr_num if rr_num else 0):.2f}<{rr_min:.2f}",
                        candidate=c,
                        symbol=symbol,
                        timeframe=timeframe,
                        direction=direction,
                        trade_mode=trade_mode,
                        rr=rr_num,
                        risk_state="RR_FAIL",
                        conn=conn,
                    )
                    try:
                        from services.metrics import inc_skip
                        inc_skip()
                    except Exception:
                        pass
                    continue

                if profit_guard["enabled"] and trade_mode == "scalping" and direction == "SHORT" and not signal_only:
                    short_rr_min = rr_min + profit_guard["short_rr_bonus"]
                    if rr_num < short_rr_min:
                        log.info(
                            "[autopost] SKIP %s/%s: profit_guard short RR=%.2f<%.2f",
                            symbol, timeframe, rr_num, short_rr_min
                        )
                        log_decision(
                            source="autopost",
                            decision="PROFIT_GUARD",
                            reason=f"profit_guard short RR={rr_num:.2f}<{short_rr_min:.2f}",
                            candidate=c,
                            symbol=symbol,
                            timeframe=timeframe,
                            direction=direction,
                            trade_mode=trade_mode,
                            rr=rr_num,
                            risk_state="PROFIT_GUARD",
                            conn=conn,
                        )
                        continue

                if short_open_blocked and not signal_only:
                    reason = "AUTOPOST_DISABLE_SHORTS=true"
                    log.info("[autopost] SKIP %s/%s: %s", symbol, timeframe, reason)
                    log_decision(
                        source="autopost",
                        decision="SHORT_DISABLED",
                        reason=reason,
                        candidate=c,
                        symbol=symbol,
                        timeframe=timeframe,
                        direction=direction,
                        trade_mode=trade_mode,
                        rr=rr_num,
                        risk_state="SHORT_DISABLED",
                        conn=conn,
                    )
                    continue
                
                ind_src: Optional[Dict[str, Any]] = c.get("ind")
                ind_sum = ind_summary(direction, entry, ind_src)
                
                panel_text = None
                df = c.get("df")
                if want_panel:
                    try:
                        if df is None or (hasattr(df, "__len__") and len(df) < 30):
                            panel_text = build_panel_lite(entry, ind_sum)
                        else:
                            from services.autopost.indicators import build_preset3_panel
                            panel_text = build_preset3_panel(df)
                            if not panel_text:
                                panel_text = build_panel_lite(entry, ind_sum)
                    except Exception as e:
                        log.debug("[autopost] panel build failed for %s/%s: %s", symbol, timeframe, e)
                        panel_text = build_panel_lite(entry, ind_sum)
                
                qscore: Optional[int] = None
                qtags: Optional[List[str]] = None
                rr_est = _parse_rr_from_reasons(c.get("reasons")) or rr_t
                if quality_on:
                    qs, tags = _qscore_basic(direction, rr_est, df)
                    qscore, qtags = qs, tags
                
                ob = None
                ob_extra_lines = None
                try:
                    ob = await get_wall_info(symbol, entry, direction)
                except Exception:
                    pass
                
                # 📊 Long/Short Ratio (Sentiment)
                sentiment = None
                try:
                    from market_data.long_short_ratio import get_sentiment_short
                    sentiment = await get_sentiment_short(symbol, period="5m")
                except Exception as e:
                    log.debug("[autopost] sentiment fetch failed for %s: %s", symbol, e)
                
                if not reserve_autopost_send(
                    user_id=user_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    rr=rr_t,
                    window_sec=dedup_sec,
                ):
                    log.info("[autopost] race-dedup %s/%s — already reserved, skip", symbol, timeframe)
                    log_decision(
                        source="autopost",
                        decision="SKIP",
                        reason="race_dedup",
                        candidate=c,
                        symbol=symbol,
                        timeframe=timeframe,
                        direction=direction,
                        trade_mode=trade_mode,
                        rr=rr_num,
                        risk_state="DEDUP",
                        conn=conn,
                    )
                    continue
                
                # Use rr_num (which includes slippage-adjusted RR) for display
                display_rr = rr_num if rr_num else rr_t
                
                text = format_message_text(
                    symbol,
                    direction,
                    timeframe,
                    entry,
                    sl,
                    tp,
                    display_rr,  # Use slippage-adjusted RR
                    ind_sum,
                    gate_score=c.get("gate_score"),
                    gate_total=c.get("gate_total"),
                    panel=panel_text,
                    reasons=c.get("reasons"),
                    qscore=(qscore if quality_on else None),
                    qtags=(qtags if quality_on else None),
                    ob=ob,
                    ob_extra_lines=ob_extra_lines,
                    sentiment=sentiment,  # 📊 Long/Short Ratio
                    full_ind=c.get("ind"),  # Full indicator data for detailed display
                    trade_mode=trade_mode,
                )
                if signal_only:
                    blockers = c.get("hard_blockers") or (c.get("ind") or {}).get("hard_blockers") or []
                    warnings = c.get("signal_only_warnings") or []
                    notice_lines = [
                        "⚠️ Сигнал без автовходу",
                        "SHORT auto-open вимкнений",
                    ]
                    if blockers:
                        notice_lines.append("🧱 Що стримує")
                        notice_lines.extend(f"• {str(x)}" for x in blockers[:4])
                    if warnings:
                        notice_lines.append("⚙️ Попередження")
                        notice_lines.extend(f"• {str(x)}" for x in warnings[:4])
                    text = "\n".join(notice_lines) + "\n━━━━━━━━━━━━━━━━━━━━\n" + text
                    _record_candidate_paper(
                        conn,
                        c,
                        source="short_signal_only",
                        trade_mode=trade_mode,
                        reason="SHORT_SIGNAL_ONLY=true",
                    )
                
                msg: Dict[str, Any] = {
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": None,
                    "disable_web_page_preview": True,
                    "symbol": symbol,
                    "direction": direction,
                    "timeframe": timeframe,
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "rr": rr_t,
                    "trade_mode": trade_mode,
                    "signal_only": signal_only,
                    "signal_only_reason": "SHORT_SIGNAL_ONLY=true" if signal_only else None,
                    "market_regime": regime,
                    # ══════════════════════════════════════════════════════════
                    # SCALPING / INDICATOR FIELDS - передаємо для запису в БД
                    # ══════════════════════════════════════════════════════════
                    "ind": c.get("ind"),  # Повний набір індикаторів
                    "reasons": c.get("reasons"),
                    "hard_blockers": c.get("hard_blockers"),
                    "gate_details": (c.get("ind") or {}).get("gate_details"),
                    "gate_score": c.get("gate_score"),
                    "gate_total": c.get("gate_total"),
                    "gate_pct": c.get("gate_pct"),
                    "slippage_pct": c.get("slippage_pct"),
                    "rr_raw": c.get("rr_raw"),
                    "rr_adj": c.get("rr_adj"),
                    "rr_target": c.get("rr_target"),
                    "buttons": [
                        [
                            {"type": "url", "text": "📊 Графік (TV)", "url": f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}"},
                        ],
                        [
                            {"type": "cb", "text": f"🤖 AI {symbol}", "data": f"ai:{symbol}"},
                            {"type": "cb", "text": f"🔗 Залежність BTC/ETH {symbol}", "data": f"dep:{symbol}"},
                        ],
                        [
                            {"type": "cb", "text": "📘 Гайд до цього сигналу", "data": "guide:signal"},
                        ],
                    ],
                }
                prepared.append(msg)
                log_decision(
                    source="autopost",
                    decision="SHORT_SIGNAL_ONLY" if signal_only else "PREPARED",
                    reason="SHORT_SIGNAL_ONLY=true" if signal_only else "ok",
                    candidate=msg,
                    symbol=symbol,
                    timeframe=timeframe,
                    direction=direction,
                    trade_mode=trade_mode,
                    rr=display_rr,
                    risk_state="READY",
                    indicators=msg.get("ind"),
                    conn=conn,
                )
            
            except Exception as e:
                log.warning("[autopost] bad candidate skipped: %s", e)
            finally:
                try:
                    conn.commit()
                except Exception:
                    pass
    
    if quality_on and prepared:
        keep = [m for m in prepared if (m.get("qscore") or 0) >= quality_min]
        keep.sort(key=lambda m: (m.get("qscore") or 0), reverse=True)
        prepared = keep[:quality_topk]
    
    log.info("[autopost] prepared %d message(s)", len(prepared))
    
    # NOTE: Sending is now handled by main.py autopost_scan() to avoid duplicates
    # The code below is kept for backward compatibility when run_autopost_once is called directly
    # but main.py already sends messages, so we skip sending here when application is passed
    
    return prepared
