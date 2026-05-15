"""Quality scoring functions."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

try:
    from services.analyzer_core import evaluate_gate
except Exception:
    evaluate_gate = None


def compute_rr_num(direction: str, entry: float, stop: float, tp: float) -> Optional[float]:
    """Compute RR number."""
    import math
    try:
        if any(math.isnan(x) for x in [entry, stop, tp]):
            return None
        if direction == "LONG":
            risk = entry - stop
            reward = tp - entry
        elif direction == "SHORT":
            risk = stop - entry
            reward = entry - tp
        else:
            return None
        if risk <= 0 or reward <= 0:
            return None
        return float(reward / risk)
    except Exception:
        return None


def quick_qscore(direction: str, rr_est: Optional[float], df) -> Tuple[int, List[str]]:
    """Quick quality score."""
    if df is None or len(df) < 20:
        return 0, ["insufficient_data"]
    
    try:
        from ta.trend import EMAIndicator, MACD, ADXIndicator
        from ta.momentum import RSIIndicator
        from ta.volatility import BollingerBands
    except Exception:
        return 0, ["ta_library_missing"]
    
    score = 0
    tags = []
    
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    
    ema50 = EMAIndicator(close, window=50).ema_indicator().iloc[-1]
    ema200 = EMAIndicator(close, window=200 if len(close) >= 200 else min(200, len(close))).ema_indicator().iloc[-1]
    
    if direction == "LONG":
        if ema50 >= ema200:
            score += 20
            tags.append("trend_up")
    else:
        if ema50 < ema200:
            score += 20
            tags.append("trend_down")
    
    macd = MACD(close)
    macd_hist = macd.macd_diff().iloc[-1]
    if (direction == "LONG" and macd_hist > 0) or (direction == "SHORT" and macd_hist < 0):
        score += 15
        tags.append("macd_ok")
    
    rsi = RSIIndicator(close, window=14).rsi().iloc[-1]
    if direction == "LONG" and rsi >= 50:
        score += 15
        tags.append("rsi_long")
    elif direction == "SHORT" and rsi <= 50:
        score += 15
        tags.append("rsi_short")
    
    adx = ADXIndicator(high, low, close, window=14).adx().iloc[-1]
    if adx >= 20:
        score += 20
        tags.append("adx_strong")
    
    bb = BollingerBands(close, window=20, window_dev=2)
    bbw = (bb.bollinger_hband().iloc[-1] - bb.bollinger_lband().iloc[-1]) / close.iloc[-1]
    if bbw >= 0.015:
        score += 15
        tags.append("volatility_ok")
    
    if rr_est is not None and rr_est >= 2.0:
        score += 15
        tags.append("rr_good")
    
    return min(100, score), tags


def gate_score(direction: str, ind: Optional[Dict[str, Any]], cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Get gate score."""
    if evaluate_gate is None:
        return {"score": None, "total": 12, "reasons": ["gate_eval_unavailable"]}
    
    if not ind:
        return {"score": 0, "total": 12, "reasons": ["no_indicators"]}
    
    return evaluate_gate(ind, direction, cfg)
