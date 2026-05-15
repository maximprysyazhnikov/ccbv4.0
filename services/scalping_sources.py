# services/scalping_sources.py
"""
Scalping mode signal generator - fixed % SL/TP with slippage.
Окрема логіка від стандартного autopost_sources.py
"""
from __future__ import annotations
import json
import os
from typing import Any, Dict, List
from urllib.request import urlopen

import pandas as pd
from utils.settings import get_setting
import logging

# Ініціалізація логера
logger = logging.getLogger("services.scalping_sources")

BINANCE_URL = "https://api.binance.com/api/v3/klines"

# ── helpers ───────────────────────────────────────────────────────────────────
def _gs(key: str, default: str = "") -> str:
    v = get_setting(key, None)
    if v in (None, ""):
        v = os.getenv(key.upper(), "")
    return v if v not in (None, "") else default

def _gs_float(key: str, default: float) -> float:
    try:
        return float(_gs(key, str(default)) or default)
    except Exception:
        return default

def _gs_bool(key: str, default: bool = False) -> bool:
    raw = str(_gs(key, "true" if default else "false")).strip().lower()
    return raw in ("1", "true", "yes", "on")

# ── data ──────────────────────────────────────────────────────────────────────
def _fetch_klines(symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
    """Fetch klines from Binance with retries and robust parsing.

    Returns empty DataFrame on failure.
    """
    url = f"{BINANCE_URL}?symbol={symbol}&interval={interval}&limit={limit}"

    # Retry logic
    last_exc = None
    for attempt in range(1, 4):
        try:
            with urlopen(url, timeout=10) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw)
            break
        except Exception as e:
            last_exc = e
            logger.warning("Fetch klines attempt %d failed for %s: %s", attempt, symbol, e)
            time_to_sleep = 0.5 * attempt
            import time as _time
            _time.sleep(time_to_sleep)
    else:
        logger.error("Failed to fetch klines for %s after retries: %s", symbol, last_exc)
        return pd.DataFrame()

    # Ensure data is a list of kline rows
    if not isinstance(data, list):
        logger.error("Unexpected klines payload for %s: %s", symbol, str(data)[:200])
        return pd.DataFrame()

    # Normalize rows: ensure each row has at least 6 elements (open, high, low, close, volume)
    normalized = []
    for row in data:
        if not isinstance(row, (list, tuple)):
            continue
        if len(row) < 6:
            logger.debug("Skipping malformed row for %s: %s", symbol, row)
            continue
        # if volume missing or empty string, try to use taker_base_vol or qav if available
        if row[5] in (None, ""):
            # try to pick a fallback if row is long enough
            vol = None
            if len(row) >= 10 and row[9] not in (None, ""):
                vol = row[9]
            elif len(row) >= 8 and row[7] not in (None, ""):
                vol = row[7]
            row = list(row)
            row[5] = vol or 0
        normalized.append(row)

    # Debugging: Log a small sample
    logger.debug("Raw klines sample for %s: %s", symbol, normalized[:3])

    try:
        df = pd.DataFrame(
            normalized,
            columns=[
                "open_time","open","high","low","close","volume",
                "close_time","qav","num_trades","taker_base_vol","taker_quote_vol","ignore"
            ],
        )

        logger.debug(f"DataFrame columns: {df.columns}")

        df = df[["open_time","open","high","low","close","volume"]].copy()
        df["ts"] = (df["open_time"] // 1000).astype(int)

        # If volume all null/zero -> log and still continue (some indicators tolerate 0)
        if "volume" not in df.columns or df["volume"].isnull().all():
            logger.error("Missing or invalid 'volume' data in DataFrame for %s. Sample: %s", symbol, normalized[:3])
            return pd.DataFrame()

        # Fill NA volume with 0
        df["volume"] = df["volume"].fillna(0)

        for col in ["open","high","low","close","volume"]:
            df[col] = df[col].astype(float)

        return df[["ts","open","high","low","close","volume"]]
    except Exception as e:
        logger.exception("Failed to parse klines for %s: %s", symbol, e)
        logger.debug("Raw data sample: %s", normalized[:5])
        return pd.DataFrame()

# ── indicators ────────────────────────────────────────────────────────────────
import numpy as np

def _ema(series: pd.Series, span: int) -> float:
    """EMA - Exponential Moving Average"""
    return float(series.ewm(span=span, adjust=False).mean().iloc[-1])

def _sma(series: pd.Series, period: int) -> float:
    """SMA - Simple Moving Average"""
    return float(series.rolling(period).mean().iloc[-1])

def _rsi(close: pd.Series, period: int = 14) -> float:
    """RSI - Relative Strength Index
    Formula: RSI = 100 - (100 / (1 + RS))
    RS = avg_gain / avg_loss over period
    """
    delta = close.diff()
    up = delta.clip(lower=0.0)
    down = (-delta).clip(lower=0.0)
    gain = up.ewm(alpha=1/period, adjust=False).mean()
    loss = down.ewm(alpha=1/period, adjust=False).mean()
    rs = gain / (loss + 1e-12)
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])

def _rsi14(close: pd.Series) -> float:
    """RSI(14) wrapper"""
    return _rsi(close, 14)

def _stoch_rsi(close: pd.Series, period: int = 14, k_period: int = 3, d_period: int = 3):
    """Stochastic RSI
    Formula: StochRSI = (RSI - RSI_min) / (RSI_max - RSI_min)
    %K = SMA(StochRSI, k_period)
    %D = SMA(%K, d_period)
    """
    delta = close.diff()
    up = delta.clip(lower=0.0)
    down = (-delta).clip(lower=0.0)
    gain = up.ewm(alpha=1/period, adjust=False).mean()
    loss = down.ewm(alpha=1/period, adjust=False).mean()
    rs = gain / (loss + 1e-12)
    rsi = 100 - (100 / (1 + rs))
    
    min_rsi = rsi.rolling(period).min()
    max_rsi = rsi.rolling(period).max()
    stoch_rsi = (rsi - min_rsi) / (max_rsi - min_rsi + 1e-12)
    k = stoch_rsi.rolling(k_period).mean() * 100
    d = k.rolling(d_period).mean()
    return float(k.iloc[-1]), float(d.iloc[-1])

def _macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD - Moving Average Convergence Divergence
    Formula: MACD = EMA(fast) - EMA(slow)
    Signal = EMA(MACD, signal)
    Histogram = MACD - Signal
    """
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return float(macd_line.iloc[-1]), float(signal_line.iloc[-1]), float(histogram.iloc[-1])

def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
    """ATR - Average True Range
    Formula: TR = max(H-L, |H-prevC|, |L-prevC|)
    ATR = EWM(TR, period)
    """
    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, adjust=False).mean()
    return float(atr.iloc[-1])

def _atr14(high: pd.Series, low: pd.Series, close: pd.Series) -> float:
    """ATR(14) wrapper"""
    return _atr(high, low, close, 14)

def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> float:
    """ADX - Average Directional Index
    Formula: ADX = EWM(|+DI - -DI| / (+DI + -DI) * 100)
    +DI = EWM(+DM) / ATR * 100
    -DI = EWM(-DM) / ATR * 100
    """
    up_move = high.diff()
    down_move = -low.diff()
    
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm = pd.Series(plus_dm, index=high.index)
    minus_dm = pd.Series(minus_dm, index=high.index)
    
    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, adjust=False).mean()
    
    plus_di = 100 * (plus_dm.ewm(alpha=1/period, adjust=False).mean() / (atr + 1e-12))
    minus_di = 100 * (minus_dm.ewm(alpha=1/period, adjust=False).mean() / (atr + 1e-12))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-12)
    adx = dx.ewm(alpha=1/period, adjust=False).mean()
    return float(adx.iloc[-1])

def _cci(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20) -> float:
    """CCI - Commodity Channel Index
    Formula: CCI = (TP - SMA(TP)) / (0.015 * MAD)
    TP = (H + L + C) / 3
    MAD = Mean Absolute Deviation
    """
    tp = (high + low + close) / 3
    sma_tp = tp.rolling(period).mean()
    mad = (tp - sma_tp).abs().rolling(period).mean()
    cci = (tp - sma_tp) / (0.015 * mad + 1e-12)
    return float(cci.iloc[-1])

def _bollinger(close: pd.Series, period: int = 20, std_mult: float = 2.0):
    """Bollinger Bands
    Formula: Middle = SMA(period)
    Upper = Middle + std_mult * STD
    Lower = Middle - std_mult * STD
    %B = (Price - Lower) / (Upper - Lower)
    """
    middle = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = middle + std_mult * std
    lower = middle - std_mult * std
    pct_b = (close - lower) / (upper - lower + 1e-12)
    return float(upper.iloc[-1]), float(middle.iloc[-1]), float(lower.iloc[-1]), float(pct_b.iloc[-1])

def _obv(close: pd.Series, volume: pd.Series) -> float:
    """OBV - On Balance Volume
    Formula: OBV += volume if close > prev_close else -volume
    """
    direction = np.sign(close.diff()).fillna(0)
    obv = (volume * direction).cumsum()
    return float(obv.iloc[-1])

def _mfi(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, period: int = 14) -> float:
    """MFI - Money Flow Index
    Formula: MFI = 100 - (100 / (1 + MFR))
    MFR = Positive Money Flow / Negative Money Flow
    """
    tp = (high + low + close) / 3
    mf = tp * volume
    pos_mf = mf.where(tp > tp.shift(1), 0.0)
    neg_mf = mf.where(tp < tp.shift(1), 0.0)
    pos_roll = pos_mf.rolling(period).sum()
    neg_roll = neg_mf.rolling(period).sum().replace(0, 1e-12)
    mfr = pos_roll / neg_roll
    mfi = 100 - (100 / (1 + mfr))
    return float(mfi.iloc[-1])

def _pivots(prev_high: float, prev_low: float, prev_close: float):
    """Pivot Points (Standard)
    Formula: P = (H + L + C) / 3
    R1 = 2*P - L, S1 = 2*P - H
    R2 = P + (H - L), S2 = P - (H - L)
    R3 = H + 2*(P - L), S3 = L - 2*(H - P)
    """
    p = (prev_high + prev_low + prev_close) / 3.0
    r1 = 2*p - prev_low
    s1 = 2*p - prev_high
    r2 = p + (prev_high - prev_low)
    s2 = p - (prev_high - prev_low)
    r3 = prev_high + 2*(p - prev_low)
    s3 = prev_low - 2*(prev_high - p)
    return {"pivot": p, "r1": r1, "s1": s1, "r2": r2, "s2": s2, "r3": r3, "s3": s3}

def _vwap(high: pd.Series, low: pd.Series, close: pd.Series, vol: pd.Series, period: int = 20) -> float:
    """VWAP - Volume Weighted Average Price
    Formula: VWAP = sum(TP * Volume) / sum(Volume)
    TP = (H + L + C) / 3
    """
    tp = (high + low + close) / 3.0
    vp = (tp * vol).rolling(period).sum()
    vv = vol.rolling(period).sum()
    vwap = vp / vv.replace(0.0, pd.NA)
    return float(vwap.iloc[-1])

def _vwap20(high: pd.Series, low: pd.Series, close: pd.Series, vol: pd.Series) -> float:
    """VWAP(20) wrapper"""
    return _vwap(high, low, close, vol, 20)

def _volume_ratio(volume: pd.Series, period: int = 20) -> float:
    """Volume ratio vs average"""
    avg = volume.rolling(period).mean().iloc[-1]
    return float(volume.iloc[-1] / avg) if avg > 0 else 1.0


def collect_all_indicators(df: pd.DataFrame, price: float) -> Dict[str, Any]:
    """
    Збирає ВСІ індикатори для аналітики.
    Повертає повний dict для збереження в БД та AI аналізу.
    """
    c = df["close"]; h = df["high"]; l = df["low"]; v = df["volume"]
    
    # Trend indicators
    ema50 = _ema(c, min(50, len(c)-1)) if len(c) > 1 else price
    ema200 = _ema(c, min(200, len(c)-1)) if len(c) > 1 else price
    sma7 = _sma(c, min(7, len(c))) if len(c) >= 7 else price
    sma25 = _sma(c, min(25, len(c))) if len(c) >= 25 else price
    
    # MACD
    macd_val, macd_signal, macd_hist = _macd(c) if len(c) >= 26 else (0, 0, 0)
    
    # RSI & StochRSI
    rsi14 = _rsi(c, 14) if len(c) >= 14 else 50
    stoch_k, stoch_d = _stoch_rsi(c) if len(c) >= 14 else (50, 50)
    
    # ADX & CCI
    adx14 = _adx(h, l, c, 14) if len(c) >= 14 else 0
    cci20 = _cci(h, l, c, 20) if len(c) >= 20 else 0
    
    # ATR
    atr14 = _atr(h, l, c, 14) if len(c) >= 14 else 0
    atr_pct = (atr14 / price * 100) if price > 0 else 0
    
    # Bollinger
    bb_upper, bb_mid, bb_lower, bb_pct_b = _bollinger(c) if len(c) >= 20 else (price, price, price, 0.5)
    
    # Volume indicators
    obv = _obv(c, v) if len(c) > 1 else 0
    mfi14 = _mfi(h, l, c, v, 14) if len(c) >= 14 else 50
    vol_ratio = _volume_ratio(v, 20) if len(v) >= 20 else 1.0
    
    # VWAP
    vwap = _vwap(h, l, c, v, 20) if len(c) >= 20 else price
    vwap_delta_pct = (abs(price - vwap) / price * 100) if price > 0 else 0
    
    # Pivots (use previous candle)
    if len(df) >= 2:
        prev = df.iloc[-2]
        pivots = _pivots(float(prev["high"]), float(prev["low"]), float(prev["close"]))
    else:
        pivots = {"pivot": price, "r1": price, "s1": price, "r2": price, "s2": price, "r3": price, "s3": price}
    
    indicators = {
        "price": price,
        # Trend
        "ema50": ema50,
        "ema200": ema200,
        "sma7": sma7,
        "sma25": sma25,
        # MACD
        "macd": macd_val,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        # RSI & StochRSI
        "rsi14": rsi14,
        "stoch_k": stoch_k,
        "stoch_d": stoch_d,
        # ADX & CCI
        "adx14": adx14,
        "cci20": cci20,
        # ATR
        "atr14": atr14,
        "atr_pct": atr_pct,
        # Bollinger
        "bb_upper": bb_upper,
        "bb_mid": bb_mid,
        "bb_lower": bb_lower,
        "bb_pct_b": bb_pct_b,
        # Volume
        "obv": obv,
        "mfi14": mfi14,
        "vol_ratio": vol_ratio,
        "volume": float(v.iloc[-1]) if len(v) > 0 else 0.0,
        # VWAP
        "vwap": vwap,
        "vwap_delta_pct": vwap_delta_pct,
        # Pivots
        **pivots,
    }

    # Додаємо логування для відстеження індикаторів
    logger.debug("Indicators for %s: %s", price, indicators)

    return indicators


# ── main scalping generator ───────────────────────────────────────────────────
def _get_scalping_symbols(custom_symbols: str = None) -> List[str]:
    """Get symbols configuration for scalping analysis."""
    from utils.user_settings import get_user_settings
    import os
    
    if custom_symbols:
        symbols_raw = custom_symbols
    else:
        user_id = os.getenv("TELEGRAM_CHAT_ID", "default")
        us = get_user_settings(user_id)
        if isinstance(us, dict) and us.get("monitored_symbols"):
            symbols_raw = us.get("monitored_symbols")
        else:
            symbols_raw = _gs("monitored_symbols", "BTCUSDT,ETHUSDT,BNBUSDT,SOLUSDT,XRPUSDT,ADAUSDT,LTCUSDT,XLMUSDT,LINKUSDT,FOGOUSDT,DOGEUSDT,AVAXUSDT,DOTUSDT,NEARUSDT,ARBUSDT,OPUSDT,SUIUSDT,APTUSDT,TONUSDT,TRXUSDT")
    
    return [s.strip().upper() for s in symbols_raw.split(",") if s.strip()]


def _get_scalping_thresholds() -> Dict[str, float]:
    """Get all threshold values for scalping gate logic."""
    return {
        # RSI thresholds
        "rsi_long_min": _gs_float("scalp_rsi_long_min", 40.0),
        "rsi_short_max": _gs_float("scalp_rsi_short_max", 60.0),
        "rsi_overbought": _gs_float("scalp_rsi_overbought", 70.0),
        "rsi_oversold": _gs_float("scalp_rsi_oversold", 30.0),
        "rsi_long_hard_max": _gs_float("scalp_rsi_long_hard_max", 70.0),
        "rsi_short_hard_min": _gs_float("scalp_rsi_short_hard_min", 30.0),
        
        # ADX thresholds
        "adx_min": _gs_float("scalp_adx_min", 20.0),
        "adx_strong": _gs_float("scalp_adx_strong", 25.0),
        
        # ATR thresholds
        "atr_pct_min": _gs_float("scalp_atr_pct_min", 0.15),
        "atr_pct_max": _gs_float("scalp_atr_pct_max", 2.0),
        
        # VWAP threshold
        "vwap_delta_max": _gs_float("scalp_vwap_delta_max", 1.5),
        "vwap_delta_long_hard_max": _gs_float("scalp_vwap_delta_long_hard_max", 0.35),
        "vwap_delta_short_hard_max": _gs_float("scalp_vwap_delta_short_hard_max", 0.35),

        # MACD threshold
        "macd_histogram_threshold": 0.0,

        # Volume threshold
        "vol_ratio_min": _gs_float("scalp_vol_ratio_min", 0.8),
        "vol_ratio_hard_min": _gs_float("scalp_vol_ratio_hard_min", 0.70),

        # Bollinger %B thresholds
        "bb_pct_b_long_max": 0.8,
        "bb_pct_b_short_min": 0.2,
        "bb_pct_b_long_hard_max": _gs_float("scalp_bb_pct_b_long_hard_max", 0.90),
        "bb_pct_b_short_hard_min": _gs_float("scalp_bb_pct_b_short_hard_min", 0.10),

        # MFI thresholds
        "mfi_overbought": 80.0,
        "mfi_oversold": 20.0,

        # Entry quality thresholds
        "stoch_long_min_gap": _gs_float("scalp_stoch_long_min_gap", 2.0),
        "stoch_short_min_gap": _gs_float("scalp_stoch_short_min_gap", 2.0),
    }


async def _collect_symbol_indicators(symbol: str, timeframe: str) -> tuple[pd.DataFrame, Dict[str, Any], float]:
    """Collect all indicators and L/S ratio for a symbol."""
    df = _fetch_klines(symbol, timeframe, limit=200)
    if df is None or df.empty:
        raise ValueError(f"No klines / failed to fetch for {symbol}")
    if len(df) < 50:
        raise ValueError(f"Insufficient data for {symbol} (only {len(df)} rows)")
    
    c = df["close"]; h = df["high"]; l = df["low"]; v = df["volume"]
    last = float(c.iloc[-1])
    
    # Collect all indicators
    ind = collect_all_indicators(df, last)
    
    # Get L/S Ratio for gate criterion
    try:
        from market_data.long_short_ratio import get_global_long_short_ratio
        ls_data = await get_global_long_short_ratio(symbol, "5m")
        if ls_data:
            ind["ls_ratio"] = float(ls_data.get("longShortRatio", 1.0))
        else:
            ind["ls_ratio"] = 1.0
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to get L/S ratio for {symbol}: {e}")
        ind["ls_ratio"] = 1.0
    
    return df, ind, last


def _calculate_position_sizes(last: float, direction: str, sl_pct: float, tp_pct: float, slippage_pct: float) -> Dict[str, float]:
    """Calculate entry, SL, TP prices with slippage adjustments."""
    sl_dist = last * (sl_pct / 100)
    tp_dist = last * (tp_pct / 100)
    slip = last * (slippage_pct / 100)
    
    if direction == "LONG":
        entry_raw = last
        entry_adj = last + slip
        sl_raw = last - sl_dist
        sl_adj = sl_raw - slip
        tp_raw = last + tp_dist
        tp_adj = tp_raw - slip
    else:  # SHORT
        entry_raw = last
        entry_adj = last - slip
        sl_raw = last + sl_dist
        sl_adj = sl_raw + slip
        tp_raw = last - tp_dist
        tp_adj = tp_raw + slip
    
    return {
        "entry_raw": entry_raw,
        "entry_adj": entry_adj,
        "sl_raw": sl_raw,
        "sl_adj": sl_adj,
        "tp_raw": tp_raw,
        "tp_adj": tp_adj,
    }


def _calculate_rr_metrics(tp_pct: float, sl_pct: float, entry_adj: float, sl_adj: float, tp_adj: float, direction: str) -> Dict[str, float]:
    """Calculate risk-reward metrics."""
    rr_raw = tp_pct / sl_pct
    
    if direction == "LONG":
        rr_adj = (tp_adj - entry_adj) / (entry_adj - sl_adj) if (entry_adj - sl_adj) > 0 else 0
    else:
        rr_adj = (entry_adj - tp_adj) / (sl_adj - entry_adj) if (sl_adj - entry_adj) > 0 else 0
    
    return {
        "rr_raw": rr_raw,
        "rr_adj": rr_adj,
    }


def _evaluate_scalping_gate(ind: Dict[str, Any], direction: str, thresholds: Dict[str, float], last: float) -> tuple[int, int, float, List[str], Dict[str, dict]]:
    """Evaluate comprehensive gate logic for scalping signals."""
    passed = 0
    total = 0
    reasons: List[str] = []
    gate_details: Dict[str, dict] = {}
    hard_blockers: List[str] = []

    # ── 1. TREND: EMA50 vs EMA200 ──
    total += 1
    trend_ema_ok = (direction == "LONG" and ind["ema50"] >= ind["ema200"]) or \
                   (direction == "SHORT" and ind["ema50"] < ind["ema200"])
    gate_details["trend_ema"] = {"ok": trend_ema_ok, "ema50": ind["ema50"], "ema200": ind["ema200"]}
    if trend_ema_ok: passed += 1
    reasons.append(f"{'✅' if trend_ema_ok else '❌'} TREND EMA50{'≥' if ind['ema50']>=ind['ema200'] else '<'}EMA200")

    logger.debug("Gate evaluation for %s: %s", last, gate_details)

    # ── 2. TREND: SMA7 vs SMA25 ──
    total += 1
    if direction == "LONG":
        trend_sma_ok = ind["sma7"] >= ind["sma25"]
    else:
        trend_sma_ok = ind["sma7"] < ind["sma25"]
    gate_details["trend_sma"] = {"ok": trend_sma_ok, "sma7": ind["sma7"], "sma25": ind["sma25"]}
    if trend_sma_ok: passed += 1
    reasons.append(f"{'✅' if trend_sma_ok else '❌'} SMA7{'≥' if ind['sma7']>=ind['sma25'] else '<'}SMA25")
    
    # ── 3. RSI ──
    total += 1
    rsi = ind["rsi14"]
    if direction == "LONG":
        rsi_ok = rsi >= thresholds["rsi_long_min"] and rsi < thresholds["rsi_overbought"]
        rsi_cond = f"{rsi:.1f} ∈ [{thresholds['rsi_long_min']:.0f}, {thresholds['rsi_overbought']:.0f})"
    else:
        rsi_ok = rsi <= thresholds["rsi_short_max"] and rsi > thresholds["rsi_oversold"]
        rsi_cond = f"{rsi:.1f} ∈ ({thresholds['rsi_oversold']:.0f}, {thresholds['rsi_short_max']:.0f}]"
    gate_details["rsi"] = {"ok": rsi_ok, "value": rsi}
    if rsi_ok: passed += 1
    reasons.append(f"{'✅' if rsi_ok else '❌'} RSI14: {rsi_cond}")
    if direction == "LONG" and rsi >= thresholds["rsi_long_hard_max"]:
        hard_blockers.append(f"RSI {rsi:.1f} >= {thresholds['rsi_long_hard_max']:.0f} (overbought LONG)")
    elif direction == "SHORT" and rsi <= thresholds["rsi_short_hard_min"]:
        hard_blockers.append(f"RSI {rsi:.1f} <= {thresholds['rsi_short_hard_min']:.0f} (oversold SHORT)")
    
    # ── 4. StochRSI ──
    total += 1
    stoch_k = ind["stoch_k"]
    stoch_d = ind["stoch_d"]
    if direction == "LONG":
        stoch_ok = stoch_k > stoch_d and stoch_k < 80  # K > D і не перекуплений
    else:
        stoch_ok = stoch_k < stoch_d and stoch_k > 20  # K < D і не перепроданий
    gate_details["stoch_rsi"] = {"ok": stoch_ok, "k": stoch_k, "d": stoch_d}
    if stoch_ok: passed += 1
    reasons.append(f"{'✅' if stoch_ok else '❌'} StochRSI K:{stoch_k:.1f} {'>' if stoch_k>stoch_d else '<'} D:{stoch_d:.1f}")
    # Hard-block only when Stoch is meaningfully against the intended direction.
    # A small K/D lead in the correct direction should stay a soft gate miss, not a hard reject.
    if direction == "LONG" and stoch_k < (stoch_d - thresholds["stoch_long_min_gap"]):
        hard_blockers.append(
            f"Stoch weak LONG: K {stoch_k:.1f} < D {stoch_d:.1f} - {thresholds['stoch_long_min_gap']:.1f}"
        )
    elif direction == "SHORT" and stoch_k > (stoch_d + thresholds["stoch_short_min_gap"]):
        hard_blockers.append(
            f"Stoch weak SHORT: K {stoch_k:.1f} > D {stoch_d:.1f} + {thresholds['stoch_short_min_gap']:.1f}"
        )
    
    # ── 5. MACD ──
    total += 1
    macd_line = ind["macd"]
    macd_signal = ind["macd_signal"]
    macd_hist = ind["macd_hist"]
    if direction == "LONG":
        macd_ok = macd_hist > thresholds["macd_histogram_threshold"] and macd_line > macd_signal
    else:
        macd_ok = macd_hist < -thresholds["macd_histogram_threshold"] and macd_line < macd_signal
    gate_details["macd"] = {"ok": macd_ok, "line": macd_line, "signal": macd_signal, "hist": macd_hist}
    if macd_ok: passed += 1
    reasons.append(f"{'✅' if macd_ok else '❌'} MACD hist:{macd_hist:.4f} {'>' if macd_hist>0 else '<'}0")
    
    # ── 6. ADX (сила тренду) ──
    total += 1
    adx = ind["adx14"]
    adx_ok = adx >= thresholds["adx_min"]
    adx_strong = adx >= thresholds["adx_strong"]
    gate_details["adx"] = {"ok": adx_ok, "value": adx, "strong": adx_strong}
    if adx_ok: passed += 1
    reasons.append(f"{'✅' if adx_ok else '❌'} ADX14: {adx:.1f} {'💪' if adx_strong else ''} (min:{thresholds['adx_min']:.0f})")
    
    # ── 7. CCI ──
    total += 1
    cci = ind["cci20"]
    if direction == "LONG":
        cci_ok = cci > -100 and cci < 200  # не в зоні перепроданості, не екстремально перекуплений
    else:
        cci_ok = cci < 100 and cci > -200
    gate_details["cci"] = {"ok": cci_ok, "value": cci}
    if cci_ok: passed += 1
    reasons.append(f"{'✅' if cci_ok else '❌'} CCI20: {cci:.1f}")
    
    # ── 8. ATR (волатильність) ──
    total += 1
    atr_pct = ind["atr_pct"]
    atr_ok = atr_pct >= thresholds["atr_pct_min"] and atr_pct <= thresholds["atr_pct_max"]
    gate_details["atr"] = {"ok": atr_ok, "value": ind["atr14"], "pct": atr_pct}
    if atr_ok: passed += 1
    reasons.append(f"{'✅' if atr_ok else '❌'} ATR: {atr_pct:.2f}% (range:{thresholds['atr_pct_min']:.2f}-{thresholds['atr_pct_max']:.1f}%)")
    
    # ── 9. Bollinger %B ──
    total += 1
    bb_pct_b = ind["bb_pct_b"]
    if direction == "LONG":
        bb_ok = bb_pct_b < thresholds["bb_pct_b_long_max"] and bb_pct_b > 0
    else:
        bb_ok = bb_pct_b > thresholds["bb_pct_b_short_min"] and bb_pct_b < 1
    gate_details["bollinger"] = {"ok": bb_ok, "pct_b": bb_pct_b, "upper": ind["bb_upper"], "lower": ind["bb_lower"]}
    if bb_ok: passed += 1
    reasons.append(f"{'✅' if bb_ok else '❌'} BB%B: {bb_pct_b:.2f}")
    if direction == "LONG" and bb_pct_b >= thresholds["bb_pct_b_long_hard_max"]:
        hard_blockers.append(f"BB%B {bb_pct_b:.2f} >= {thresholds['bb_pct_b_long_hard_max']:.2f} (late LONG)")
    elif direction == "SHORT" and bb_pct_b <= thresholds["bb_pct_b_short_hard_min"]:
        hard_blockers.append(f"BB%B {bb_pct_b:.2f} <= {thresholds['bb_pct_b_short_hard_min']:.2f} (late SHORT)")
    
    # ── 10. VWAP ──
    total += 1
    vwap_delta = ind["vwap_delta_pct"]
    vwap_ok = abs(vwap_delta) <= thresholds["vwap_delta_max"]
    # Додаткова перевірка: для LONG ціна має бути вище або близько до VWAP
    if direction == "LONG" and vwap_delta < -thresholds["vwap_delta_max"]:
        vwap_ok = False  # занадто низько під VWAP для LONG
    elif direction == "SHORT" and vwap_delta > thresholds["vwap_delta_max"]:
        vwap_ok = False  # занадто високо над VWAP для SHORT
    gate_details["vwap"] = {"ok": vwap_ok, "value": ind["vwap"], "delta_pct": vwap_delta}
    if vwap_ok: passed += 1
    reasons.append(f"{'✅' if vwap_ok else '❌'} VWAPΔ: {vwap_delta:+.2f}% (max:{thresholds['vwap_delta_max']:.1f}%)")
    if direction == "LONG" and vwap_delta >= thresholds["vwap_delta_long_hard_max"]:
        hard_blockers.append(
            f"VWAPΔ {vwap_delta:+.2f}% >= {thresholds['vwap_delta_long_hard_max']:.2f}% (stretched LONG)"
        )
    elif direction == "SHORT" and vwap_delta <= -thresholds["vwap_delta_short_hard_max"]:
        hard_blockers.append(
            f"VWAPΔ {vwap_delta:+.2f}% <= -{thresholds['vwap_delta_short_hard_max']:.2f}% (stretched SHORT)"
        )
    
    # ── 11. Volume Ratio ──
    total += 1
    vol_ratio = ind["vol_ratio"]
    vol_ok = vol_ratio >= thresholds["vol_ratio_min"]
    gate_details["volume"] = {"ok": vol_ok, "ratio": vol_ratio, "current": ind["volume"]}
    if vol_ok: passed += 1
    reasons.append(f"{'✅' if vol_ok else '❌'} Vol ratio: {vol_ratio:.2f}x (min:{thresholds['vol_ratio_min']:.1f})")
    if vol_ratio < thresholds["vol_ratio_hard_min"]:
        hard_blockers.append(f"Vol ratio {vol_ratio:.2f} < {thresholds['vol_ratio_hard_min']:.2f}")
    
    # ── 12. MFI (Money Flow Index) ──
    total += 1
    mfi = ind["mfi14"]
    if direction == "LONG":
        mfi_ok = mfi < thresholds["mfi_overbought"] and mfi > 20
    else:
        mfi_ok = mfi > thresholds["mfi_oversold"] and mfi < 80
    gate_details["mfi"] = {"ok": mfi_ok, "value": mfi}
    if mfi_ok: passed += 1
    reasons.append(f"{'✅' if mfi_ok else '❌'} MFI14: {mfi:.1f}")
    
    # ── 13. Pivot Points - позиція відносно рівнів ──
    total += 1
    pivot = ind["pivot"]
    r1, s1 = ind["r1"], ind["s1"]
    if direction == "LONG":
        # Ціна має бути вище S1 (підтримка), бажано вище Pivot
        pivot_ok = last >= s1
        pivot_detail = f"Price:{last:.2f} ≥ S1:{s1:.2f}"
    else:
        # Ціна має бути нижче R1 (опір), бажано нижче Pivot
        pivot_ok = last <= r1
        pivot_detail = f"Price:{last:.2f} ≤ R1:{r1:.2f}"
    gate_details["pivots"] = {"ok": pivot_ok, "pivot": pivot, "r1": r1, "s1": s1, "price": last}
    if pivot_ok: passed += 1
    reasons.append(f"{'✅' if pivot_ok else '❌'} Pivots: {pivot_detail}")
    r2 = ind["r2"]
    s2 = ind["s2"]
    gate_details["late_entry"] = {"ok": True, "r2": r2, "s2": s2}
    if direction == "LONG" and last >= r2:
        hard_blockers.append(f"Late LONG near/above R2 ({last:.4f} >= {r2:.4f})")
        gate_details["late_entry"] = {"ok": False, "r2": r2, "price": last}
    elif direction == "SHORT" and last <= s2:
        hard_blockers.append(f"Late SHORT near/below S2 ({last:.4f} <= {s2:.4f})")
        gate_details["late_entry"] = {"ok": False, "s2": s2, "price": last}
    
    # ── 14. L/S Ratio - sentiment analysis ──
    total += 1
    ls_ratio = ind["ls_ratio"]
    if direction == "LONG":
        # Для LONG потрібен bullish sentiment (більше лонгів)
        ls_ok = ls_ratio > 1.05  # >5% більше лонгів ніж шортів
        ls_detail = f"L/S:{ls_ratio:.3f} >1.05"
    else:
        # Для SHORT потрібен bearish sentiment (більше шортів)
        ls_ok = ls_ratio < 0.95  # >5% більше шортів ніж лонгів
        ls_detail = f"L/S:{ls_ratio:.3f} <0.95"
    gate_details["ls_ratio"] = {"ok": ls_ok, "ratio": ls_ratio}
    if ls_ok: passed += 1
    reasons.append(f"{'✅' if ls_ok else '❌'} L/S Ratio: {ls_detail}")
    gate_pct = (passed / total * 100) if total > 0 else 0
    hard_blockers, momentum_info = _apply_long_momentum_mode(
        ind, direction, passed, total, gate_pct, hard_blockers
    )
    gate_details["long_momentum"] = momentum_info
    gate_details["hard_blockers"] = {"ok": len(hard_blockers) == 0, "items": hard_blockers}
    if hard_blockers:
        reasons.append("🛑 Hard blockers: " + "; ".join(hard_blockers))
    elif momentum_info.get("active"):
        allowed = "; ".join(momentum_info.get("allowed_blockers", []))
        reasons.append(f"🚀 LONG momentum-mode: allowed continuation blockers: {allowed}")

    return passed, total, gate_pct, reasons, gate_details


def _is_long_momentum_blocker(blocker: str) -> bool:
    return (
        "BB%B" in blocker and "(late LONG)" in blocker
    ) or (
        "VWAP" in blocker and "(stretched LONG)" in blocker
    ) or (
        "Late LONG near/above R2" in blocker
    )


def _apply_long_momentum_mode(
    ind: Dict[str, Any],
    direction: str,
    gate_score: int,
    gate_total: int,
    gate_pct: float,
    hard_blockers: List[str],
) -> tuple[List[str], Dict[str, Any]]:
    """Allow selected LONG continuation blockers when market quality is strong.

    This does not forgive weak volume, overbought RSI, or Stoch moving against the
    entry. It only lets already-strong LONG candidates continue through
    late/stretched filters that are often expected during breakouts.
    """
    info: Dict[str, Any] = {
        "active": False,
        "enabled": _gs_bool("scalp_long_momentum_enabled", True),
        "allowed_blockers": [],
        "remaining_blockers": list(hard_blockers),
    }
    if not info["enabled"] or direction != "LONG" or not hard_blockers:
        return hard_blockers, info

    allowed = [b for b in hard_blockers if _is_long_momentum_blocker(str(b))]
    remaining = [b for b in hard_blockers if not _is_long_momentum_blocker(str(b))]
    info["allowed_blockers"] = allowed
    info["remaining_blockers"] = remaining
    if not allowed or remaining:
        return hard_blockers, info

    min_gate_pct = _gs_float("scalp_long_momentum_gate_pct", 70.0)
    min_adx = _gs_float("scalp_long_momentum_min_adx", 22.0)
    min_vol_ratio = _gs_float("scalp_long_momentum_min_vol_ratio", 0.90)
    max_rsi = _gs_float("scalp_long_momentum_max_rsi", 72.0)
    require_stoch_up = _gs_bool("scalp_long_momentum_require_stoch_up", True)

    rsi = float(ind.get("rsi14") or 0.0)
    adx = float(ind.get("adx14") or 0.0)
    vol_ratio = float(ind.get("vol_ratio") or 0.0)
    stoch_k = float(ind.get("stoch_k") or 0.0)
    stoch_d = float(ind.get("stoch_d") or 0.0)

    checks = {
        "gate_pct": gate_pct >= min_gate_pct,
        "adx": adx >= min_adx,
        "vol_ratio": vol_ratio >= min_vol_ratio,
        "rsi": rsi <= max_rsi,
        "stoch": (stoch_k >= stoch_d) if require_stoch_up else True,
    }
    info.update(
        {
            "min_gate_pct": min_gate_pct,
            "gate_score": gate_score,
            "gate_total": gate_total,
            "gate_pct": gate_pct,
            "min_adx": min_adx,
            "adx": adx,
            "min_vol_ratio": min_vol_ratio,
            "vol_ratio": vol_ratio,
            "max_rsi": max_rsi,
            "rsi": rsi,
            "require_stoch_up": require_stoch_up,
            "stoch_k": stoch_k,
            "stoch_d": stoch_d,
            "checks": checks,
        }
    )
    if all(checks.values()):
        info["active"] = True
        info["remaining_blockers"] = []
        return [], info
    return hard_blockers, info


def _format_scalping_signal(
    symbol: str, timeframe: str, direction: str, ind: Dict[str, Any],
    positions: Dict[str, float], rr_metrics: Dict[str, float], 
    gate_score: int, gate_total: int, gate_pct: float,
    reasons: List[str], gate_details: Dict[str, dict], df: pd.DataFrame,
    slippage_pct: float
) -> Dict[str, Any]:
    """Format the final scalping signal dictionary."""
    # Add gate info to indicators
    ind["gate_score"] = gate_score
    ind["gate_total"] = gate_total
    ind["gate_pct"] = gate_pct
    ind["gate_details"] = gate_details
    ind["hard_blockers"] = gate_details.get("hard_blockers", {}).get("items", [])
    momentum_info = gate_details.get("long_momentum") or {}
    
    # Add gate summary to reasons
    reasons.append(f"─────────────────────")
    reasons.append(f"🎯 GATE: {gate_score}/{gate_total} ({gate_pct:.0f}%)")
    reasons.append(f"⚡ SCALP SL:{positions['sl_raw']:.2f}% TP:{positions['tp_raw']:.2f}% Slip:{slippage_pct}%")
    reasons.append(f"📊 RR_raw={rr_metrics['rr_raw']:.2f} RR_adj={rr_metrics['rr_adj']:.2f}")
    
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "entry": positions["entry_raw"],
        "entry_adj": positions["entry_adj"],
        "sl": positions["sl_adj"],
        "sl_raw": positions["sl_raw"],
        "tp": positions["tp_adj"],
        "tp_raw": positions["tp_raw"],
        "rr_raw": rr_metrics["rr_raw"],
        "rr_adj": rr_metrics["rr_adj"],
        "rr_target": rr_metrics["rr_adj"],
        "slippage_pct": slippage_pct,
        "trade_mode": "scalping",
        "ind": ind,  # ПОВНИЙ набір індикаторів!
        "gate_score": gate_score,
        "gate_total": gate_total,
        "gate_pct": gate_pct,
        "hard_blockers": ind.get("hard_blockers", []),
        "long_momentum_mode": bool(momentum_info.get("active")),
        "long_momentum_gate_pct": momentum_info.get("min_gate_pct"),
        "long_momentum_info": momentum_info,
        "reasons": reasons,
        "df": df,
    }


async def collect_scalping_candidates(
    sl_pct: float = 0.3,
    tp_pct: float = 0.9,
    slippage_pct: float = 0.05,
    timeframe: str = "5m",
    custom_symbols: str = None,
) -> List[Dict[str, Any]]:
    """
    Генерує сигнали для скальпінгу з фіксованими % рівнями.
    Збирає ВСІ індикатори та застосовує повну gate логіку.
    
    Args:
        sl_pct: відстань до SL в % (0.3 = 0.3%)
        tp_pct: відстань до TP в % (0.9 = 0.9%)
        slippage_pct: прослизання в % (0.05 = 0.05%)
        timeframe: таймфрейм для аналізу (5m для скальпінгу)
        custom_symbols: custom symbols string (comma-separated)
    
    Returns:
        List of signal candidates with ALL indicators
    """
    # Get configuration
    symbols = _get_scalping_symbols(custom_symbols)
    thresholds = _get_scalping_thresholds()
    
    out: List[Dict[str, Any]] = []
    
    for sym in symbols:
        try:
            # Collect indicators and data
            df, ind, last = await _collect_symbol_indicators(sym, timeframe)
            
            # Determine direction based on EMA trend
            direction = "LONG" if ind["ema50"] >= ind["ema200"] else "SHORT"
            
            # Calculate position sizes with slippage
            positions = _calculate_position_sizes(last, direction, sl_pct, tp_pct, slippage_pct)
            
            # Calculate RR metrics
            rr_metrics = _calculate_rr_metrics(tp_pct, sl_pct, positions["entry_adj"], positions["sl_adj"], positions["tp_adj"], direction)
            
            # Skip if RR is too low after slippage
            if rr_metrics["rr_adj"] < 1.5:
                continue
            
            # Evaluate gate logic
            gate_score, gate_total, gate_pct, reasons, gate_details = _evaluate_scalping_gate(ind, direction, thresholds, last)
            
            # Format and add signal
            signal = _format_scalping_signal(
                sym, timeframe, direction, ind, positions, rr_metrics,
                gate_score, gate_total, gate_pct, reasons, gate_details, df, slippage_pct
            )
            out.append(signal)
            
        except Exception as e:
            # log with traceback for easier debugging
            logger.warning("Error processing %s: %s", sym, e, exc_info=True)
            continue
    
    return out
