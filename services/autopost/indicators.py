"""Indicator summary and panel functions."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.autopost.formatting import fmt_num, fmt_pct


def ind_summary(direction: str, entry: float, ind: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Create indicator summary."""
    out = {"trend_ok": None, "atr_pct": None, "vwap_pct": None, "rsi": None, "ema50": None, "ema200": None}
    if not ind:
        return out
    
    ema50 = ind.get("ema50")
    ema200 = ind.get("ema200")
    vwap = ind.get("vwap")
    vwap_dist = ind.get("vwap_dist")
    atr = ind.get("atr")
    atr_pct = ind.get("atr_pct")
    rsi = ind.get("rsi14") if ind.get("rsi14") is not None else ind.get("rsi")
    
    trend_ok = None
    try:
        if ema50 is not None and ema200 is not None:
            trend_ok = (ema50 >= ema200) if direction == "LONG" else (ema50 < ema200)
    except Exception:
        pass
    
    atr_pct_val = None
    try:
        if atr_pct is not None:
            atr_pct_val = float(atr_pct) * (100.0 if atr_pct < 1 else 1.0)
        elif atr is not None and entry:
            atr_pct_val = float(atr) / float(entry) * 100.0
    except Exception:
        pass
    
    vwap_pct = None
    try:
        if vwap_dist is not None:
            vwap_pct = float(vwap_dist) * (100.0 if vwap_dist < 1 else 1.0)
        elif vwap is not None and entry:
            vwap_pct = abs(float(entry) - float(vwap)) / float(entry) * 100.0
    except Exception:
        pass
    
    out.update({
        "trend_ok": trend_ok,
        "atr_pct": atr_pct_val,
        "vwap_pct": vwap_pct,
        "rsi": float(rsi) if rsi is not None else None,
        "ema50": float(ema50) if ema50 is not None else None,
        "ema200": float(ema200) if ema200 is not None else None,
    })
    return out


def build_panel_lite(entry: Optional[float], ind_sum: Dict[str, Any]) -> str:
    """Build lite panel text."""
    def fp(x: Optional[float]) -> str:
        if x is None:
            return "-"
        dec = 2 if abs(float(x)) >= 100 else 6
        return fmt_num(float(x), dec)
    
    trend_flag = ind_sum.get("trend_ok")
    ema50 = ind_sum.get("ema50")
    ema200 = ind_sum.get("ema200")
    rsi = ind_sum.get("rsi")
    atr_pct = ind_sum.get("atr_pct")
    vwap_pct = ind_sum.get("vwap_pct")
    
    lines = [
        "— preset3 lite —",
        f"💵 Price≈ {fp(entry)}",
        "📈 Trend: " + ("🟢" if trend_flag is True else ("🔴" if trend_flag is False else "–"))
        + f"  EMA50={fp(ema50)}  EMA200={fp(ema200)}",
        f"🎚 RSI(14): {'-' if rsi is None else f'{rsi:.1f}'}",
        f"🌊 ATR%: {fmt_pct(atr_pct)}",
        f"🎯 VWAPΔ%: {fmt_pct(vwap_pct)}",
    ]
    return "\n".join(lines)
