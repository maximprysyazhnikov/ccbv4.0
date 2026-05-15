from __future__ import annotations

import json
import math
import time
from typing import Dict, List, Tuple
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from core_config import CFG
from utils.ta_formatter import (
    _adx,
    _atr,
    _bb,
    _cci,
    _ema,
    _fmt,
    _macd,
    _mfi,
    _obv,
    _pivots,
    _rsi,
    _stochrsi,
)


YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

METAL_ALIASES: Dict[str, Tuple[str, str]] = {
    "XAU": ("GC=F", "Gold"),
    "XAUUSD": ("GC=F", "Gold"),
    "GOLD": ("GC=F", "Gold"),
    "GC=F": ("GC=F", "Gold"),
    "XAG": ("SI=F", "Silver"),
    "XAGUSD": ("SI=F", "Silver"),
    "SILVER": ("SI=F", "Silver"),
    "SI=F": ("SI=F", "Silver"),
    "XPT": ("PL=F", "Platinum"),
    "XPTUSD": ("PL=F", "Platinum"),
    "PLATINUM": ("PL=F", "Platinum"),
    "PL=F": ("PL=F", "Platinum"),
    "XPD": ("PA=F", "Palladium"),
    "XPDUSD": ("PA=F", "Palladium"),
    "PALLADIUM": ("PA=F", "Palladium"),
    "PA=F": ("PA=F", "Palladium"),
}

DEFAULT_METALS = "XAUUSD,XAGUSD,XPTUSD,XPDUSD"

_CACHE: Dict[tuple, dict] = {}
TTL_SEC = 60


def normalize_metal_symbol(symbol: str) -> Tuple[str, str, str]:
    key = (symbol or "").strip().upper()
    yahoo, label = METAL_ALIASES.get(key, (key, key))
    display = {
        "GC=F": "XAUUSD",
        "SI=F": "XAGUSD",
        "PL=F": "XPTUSD",
        "PA=F": "XPDUSD",
    }.get(yahoo, key)
    return yahoo, display, label


def parse_metals(raw: str | None) -> List[str]:
    text = (raw or DEFAULT_METALS).strip()
    out: List[str] = []
    for part in text.replace(";", ",").split(","):
        sym = part.strip().upper()
        if sym and sym not in out:
            out.append(sym)
    return out or ["XAUUSD", "XAGUSD", "XPTUSD", "XPDUSD"]


def _env_float(name: str, default: float) -> float:
    import os

    try:
        return float(os.getenv(name, str(default)) or default)
    except Exception:
        return default


def _env_int(name: str, default: int) -> int:
    import os

    try:
        return int(float(os.getenv(name, str(default)) or default))
    except Exception:
        return default


def _tf_to_yahoo(timeframe: str) -> Tuple[str, str]:
    tf = (timeframe or "1h").strip().lower()
    mapping = {
        "1m": ("1m", "1d"),
        "5m": ("5m", "5d"),
        "15m": ("15m", "10d"),
        "30m": ("30m", "1mo"),
        "1h": ("60m", "3mo"),
        "4h": ("60m", "6mo"),
        "1d": ("1d", "1y"),
    }
    return mapping.get(tf, ("60m", "3mo"))


def get_metals_ohlcv(symbol: str, timeframe: str = "1h", limit: int = 150) -> List[dict]:
    yahoo_symbol, _, _ = normalize_metal_symbol(symbol)
    interval, data_range = _tf_to_yahoo(timeframe)
    key = (yahoo_symbol, interval, data_range, int(limit))
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit["ts"] <= TTL_SEC:
        return hit["data"]

    url = YAHOO_CHART_URL.format(symbol=yahoo_symbol)
    query = urlencode(
        {
            "interval": interval,
            "range": data_range,
            "includePrePost": "false",
        }
    )
    req = Request(
        f"{url}?{query}",
        headers={"User-Agent": "Mozilla/5.0 CCBV3.8 metals monitor"},
    )
    with urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    result = ((payload.get("chart") or {}).get("result") or [None])[0]
    if not result:
        return []

    ts = result.get("timestamp") or []
    quote = (((result.get("indicators") or {}).get("quote") or [{}])[0]) or {}
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []

    rows: List[dict] = []
    for i, t in enumerate(ts):
        try:
            o = opens[i]
            h = highs[i]
            l = lows[i]
            c = closes[i]
            if None in (o, h, l, c):
                continue
            v = volumes[i] if i < len(volumes) and volumes[i] is not None else 0
            rows.append(
                {
                    "ts": int(t) * 1000,
                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(c),
                    "volume": float(v),
                }
            )
        except Exception:
            continue

    data = rows[-int(limit) :]
    _CACHE[key] = {"ts": now, "data": data}
    return data


def _badge_trend(ema50: float, ema200: float) -> str:
    if ema50 > ema200:
        return "bullish"
    if ema50 < ema200:
        return "bearish"
    return "flat"


def _direction_from_values(vals: Dict[str, float]) -> str:
    bullish = 0
    bearish = 0
    if vals["ema50"] > vals["ema200"]:
        bullish += 1
    else:
        bearish += 1
    if vals["macd"] > vals["macd_sig"]:
        bullish += 1
    else:
        bearish += 1
    if vals["rsi"] >= 55:
        bullish += 1
    elif vals["rsi"] <= 45:
        bearish += 1
    if vals["close"] > vals["vwap"]:
        bullish += 1
    else:
        bearish += 1
    if bullish >= bearish + 2:
        return "LONG bias"
    if bearish >= bullish + 2:
        return "SHORT bias"
    return "NEUTRAL"


def _latest_metals_values(rows: List[dict]) -> Tuple[pd.DataFrame, Dict[str, float], Dict[str, float]]:
    df = pd.DataFrame(rows).dropna().reset_index(drop=True)
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume_raw = df["volume"]
    volume = volume_raw.replace(0, math.nan)

    ema50 = _ema(close, 50)
    ema200 = _ema(close, 200)
    macd, macd_sig, macd_hist = _macd(close)
    rsi = _rsi(close, 14)
    stoch_k, stoch_d = _stochrsi(close)
    adx = _adx(high, low, close, 14)
    cci = _cci(high, low, close, 20)
    atr = _atr(high, low, close, 14)
    atr_pct = (atr / close) * 100
    bb_u, bb_m, bb_l, pct_b = _bb(close)
    obv = _obv(close, volume_raw)
    mfi = _mfi(high, low, close, volume_raw, 14)
    typical = (high + low + close) / 3
    vol_nonzero = volume_raw.where(volume_raw > 0)
    vwap = ((typical * vol_nonzero).cumsum() / vol_nonzero.cumsum()).ffill()
    vol_avg = volume.rolling(20).mean()
    vol_ratio = volume.iloc[-1] / vol_avg.iloc[-1] if not math.isnan(vol_avg.iloc[-1]) else math.nan

    prev = df.iloc[-2] if len(df) >= 2 else df.iloc[-1]
    piv = _pivots(prev["high"], prev["low"], prev["close"])
    vals = {
        "close": close.iloc[-1],
        "ema50": ema50.iloc[-1],
        "ema200": ema200.iloc[-1],
        "macd": macd.iloc[-1],
        "macd_sig": macd_sig.iloc[-1],
        "macd_hist": macd_hist.iloc[-1],
        "rsi": rsi.iloc[-1],
        "stoch_k": stoch_k.iloc[-1],
        "stoch_d": stoch_d.iloc[-1],
        "adx": adx.iloc[-1],
        "cci": cci.iloc[-1],
        "atr": atr.iloc[-1],
        "atr_pct": atr_pct.iloc[-1],
        "pct_b": pct_b.iloc[-1],
        "obv": obv.iloc[-1],
        "mfi": mfi.iloc[-1],
        "vwap": vwap.iloc[-1],
        "vol_ratio": vol_ratio,
    }
    return df, vals, piv


def _metals_scalp_decision(vals: Dict[str, float], piv: Dict[str, float]) -> Dict[str, object]:
    price = float(vals["close"])
    bullish = 0
    bearish = 0

    checks = []

    def add(name: str, long_ok: bool, short_ok: bool):
        nonlocal bullish, bearish
        if long_ok:
            bullish += 1
        if short_ok:
            bearish += 1
        checks.append((name, long_ok, short_ok))

    add("trend_ema", vals["ema50"] > vals["ema200"], vals["ema50"] < vals["ema200"])
    add("macd", vals["macd"] > vals["macd_sig"], vals["macd"] < vals["macd_sig"])
    add("rsi", vals["rsi"] >= 52, vals["rsi"] <= 48)
    add("stoch", vals["stoch_k"] >= vals["stoch_d"], vals["stoch_k"] <= vals["stoch_d"])
    add("adx", vals["adx"] >= _env_float("METALS_SCALP_MIN_ADX", 18), vals["adx"] >= _env_float("METALS_SCALP_MIN_ADX", 18))
    add("cci", vals["cci"] > 0, vals["cci"] < 0)
    add("vwap", price >= vals["vwap"], price <= vals["vwap"])
    add("bb_pct_b", vals["pct_b"] >= 0.45, vals["pct_b"] <= 0.55)
    add("mfi", vals["mfi"] >= 45, vals["mfi"] <= 55)
    add("pivot", price >= piv["pivot"], price <= piv["pivot"])

    direction = "LONG" if bullish >= bearish else "SHORT"
    score = bullish if direction == "LONG" else bearish
    total = len(checks)
    gate_pct = (score / total * 100) if total else 0.0

    max_rsi_long = _env_float("METALS_SCALP_MAX_RSI_LONG", 72)
    min_rsi_short = _env_float("METALS_SCALP_MIN_RSI_SHORT", 28)
    hard: List[str] = []
    if direction == "LONG":
        if vals["rsi"] >= max_rsi_long:
            hard.append(f"RSI {vals['rsi']:.1f} >= {max_rsi_long:.0f} (overbought LONG)")
        if vals["pct_b"] >= 0.92:
            hard.append(f"BB%B {vals['pct_b']:.2f} >= 0.92 (late LONG)")
        if vals["stoch_k"] < vals["stoch_d"]:
            hard.append(f"Stoch weak LONG: K {vals['stoch_k']:.1f} < D {vals['stoch_d']:.1f}")
    else:
        if vals["rsi"] <= min_rsi_short:
            hard.append(f"RSI {vals['rsi']:.1f} <= {min_rsi_short:.0f} (oversold SHORT)")
        if vals["pct_b"] <= 0.08:
            hard.append(f"BB%B {vals['pct_b']:.2f} <= 0.08 (late SHORT)")
        if vals["stoch_k"] > vals["stoch_d"]:
            hard.append(f"Stoch weak SHORT: K {vals['stoch_k']:.1f} > D {vals['stoch_d']:.1f}")

    if vals["adx"] < _env_float("METALS_SCALP_MIN_ADX", 18):
        hard.append(f"ADX {vals['adx']:.1f} < {_env_float('METALS_SCALP_MIN_ADX', 18):.0f}")

    sl_pct = _env_float("METALS_SCALP_SL_PCT", 0.35)
    tp_pct = _env_float("METALS_SCALP_TP_PCT", 0.90)
    rr = tp_pct / sl_pct if sl_pct else 0.0
    if direction == "LONG":
        sl = price * (1 - sl_pct / 100)
        tp = price * (1 + tp_pct / 100)
    else:
        sl = price * (1 + sl_pct / 100)
        tp = price * (1 - tp_pct / 100)

    min_gate = _env_float("METALS_SCALP_MIN_GATE_PCT", 72)
    final = gate_pct >= min_gate and not hard
    return {
        "direction": direction,
        "score": score,
        "total": total,
        "gate_pct": gate_pct,
        "min_gate": min_gate,
        "hard": hard,
        "final": final,
        "entry": price,
        "sl": sl,
        "tp": tp,
        "rr": rr,
        "sl_pct": sl_pct,
        "tp_pct": tp_pct,
    }


def format_metals_scalp_report(
    symbols: List[str] | None = None,
    timeframe: str | None = None,
    limit: int = 180,
) -> str:
    tf = timeframe or __import__("os").getenv("METALS_SCALP_TIMEFRAME", "5m")
    symbols = symbols or parse_metals(",".join(CFG.get("metals_symbols", []) or []))
    lines: List[str] = [f"*Precious Metals Scalper* *(TF={tf})*"]
    passed = 0
    for raw_symbol in symbols:
        yahoo_symbol, display, label = normalize_metal_symbol(raw_symbol)
        rows = get_metals_ohlcv(raw_symbol, tf, limit)
        if not rows:
            lines.append(f"\n*{display}* `{yahoo_symbol}` — no data")
            continue
        if len(rows) < 60:
            lines.append(f"\n*{display}* `{yahoo_symbol}` — not enough candles ({len(rows)})")
            continue
        _, vals, piv = _latest_metals_values(rows)
        decision = _metals_scalp_decision(vals, piv)
        if decision["final"]:
            passed += 1
        status = "PASS" if decision["final"] else "SKIP"
        hard = "; ".join(decision["hard"]) if decision["hard"] else "-"
        lines.append(
            "\n".join(
                [
                    "",
                    f"*{display}* `{yahoo_symbol}` — {label}",
                    f"- {status}: *{decision['direction']}* gate `{decision['score']}/{decision['total']}` ({decision['gate_pct']:.0f}%, need {decision['min_gate']:.0f}%) RR `{decision['rr']:.2f}`",
                    f"- Entry `{_fmt(decision['entry'], 2)}` | SL `{_fmt(decision['sl'], 2)}` | TP `{_fmt(decision['tp'], 2)}`",
                    f"- RSI `{_fmt(vals['rsi'], 1)}` | Stoch K/D `{_fmt(vals['stoch_k'], 1)}` / `{_fmt(vals['stoch_d'], 1)}` | ADX `{_fmt(vals['adx'], 1)}`",
                    f"- VWAP `{_fmt(vals['vwap'], 2)}` | BB%B `{_fmt(vals['pct_b'], 3)}` | Vol x`{_fmt(vals['vol_ratio'], 2)}`",
                    f"- Hard: {hard}",
                ]
            )
        )
    lines.append(f"\nSummary: `{passed}/{len(symbols)}` final candidates")
    return "\n".join(lines)


def collect_metals_scalp_candidates(
    symbols: List[str] | None = None,
    timeframe: str | None = None,
    limit: int = 180,
) -> List[Dict[str, object]]:
    tf = timeframe or __import__("os").getenv("METALS_SCALP_TIMEFRAME", "5m")
    symbols = symbols or parse_metals(",".join(CFG.get("metals_symbols", []) or []))
    out: List[Dict[str, object]] = []
    for raw_symbol in symbols:
        yahoo_symbol, display, label = normalize_metal_symbol(raw_symbol)
        rows = get_metals_ohlcv(raw_symbol, tf, limit)
        if len(rows) < 60:
            out.append(
                {
                    "symbol": display,
                    "yahoo_symbol": yahoo_symbol,
                    "label": label,
                    "timeframe": tf,
                    "ok": False,
                    "reason": f"not_enough_candles:{len(rows)}",
                }
            )
            continue
        _, vals, piv = _latest_metals_values(rows)
        decision = _metals_scalp_decision(vals, piv)
        out.append(
            {
                "symbol": display,
                "yahoo_symbol": yahoo_symbol,
                "label": label,
                "timeframe": tf,
                "ok": True,
                "final": bool(decision["final"]),
                "direction": decision["direction"],
                "entry": float(decision["entry"]),
                "sl": float(decision["sl"]),
                "tp": float(decision["tp"]),
                "rr": float(decision["rr"]),
                "gate_score": int(decision["score"]),
                "gate_total": int(decision["total"]),
                "gate_pct": float(decision["gate_pct"]),
                "min_gate": float(decision["min_gate"]),
                "hard_blockers": list(decision["hard"]),
                "sl_pct": float(decision["sl_pct"]),
                "tp_pct": float(decision["tp_pct"]),
                "ind": {
                    "price": float(vals["close"]),
                    "ema50": float(vals["ema50"]),
                    "ema200": float(vals["ema200"]),
                    "macd": float(vals["macd"]),
                    "macd_signal": float(vals["macd_sig"]),
                    "macd_hist": float(vals["macd_hist"]),
                    "rsi14": float(vals["rsi"]),
                    "stoch_k": float(vals["stoch_k"]),
                    "stoch_d": float(vals["stoch_d"]),
                    "adx14": float(vals["adx"]),
                    "cci20": float(vals["cci"]),
                    "atr14": float(vals["atr"]),
                    "atr_pct": float(vals["atr_pct"]),
                    "bb_pct_b": float(vals["pct_b"]),
                    "vwap": float(vals["vwap"]),
                    "vol_ratio": None if math.isnan(float(vals["vol_ratio"])) else float(vals["vol_ratio"]),
                    "pivot": float(piv["pivot"]),
                    "r1": float(piv["r1"]),
                    "r2": float(piv["r2"]),
                    "s1": float(piv["s1"]),
                    "s2": float(piv["s2"]),
                },
            }
        )
    return out


def format_metals_report(symbols: List[str], timeframe: str = "1h", limit: int = 150) -> str:
    blocks: List[str] = [f"*Precious Metals* *(TF={timeframe})*"]
    for raw_symbol in symbols:
        yahoo_symbol, display, label = normalize_metal_symbol(raw_symbol)
        rows = get_metals_ohlcv(raw_symbol, timeframe, limit)
        if not rows:
            blocks.append(f"\n*{display}* — no data from `{yahoo_symbol}`")
            continue

        df = pd.DataFrame(rows).dropna().reset_index(drop=True)
        if len(df) < 30:
            blocks.append(f"\n*{display}* — not enough candles ({len(df)})")
            continue

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"].replace(0, math.nan)

        ema50 = _ema(close, 50)
        ema200 = _ema(close, 200)
        macd, macd_sig, macd_hist = _macd(close)
        rsi = _rsi(close, 14)
        stoch_k, stoch_d = _stochrsi(close)
        adx = _adx(high, low, close, 14)
        cci = _cci(high, low, close, 20)
        atr = _atr(high, low, close, 14)
        atr_pct = (atr / close) * 100
        bb_u, bb_m, bb_l, pct_b = _bb(close)
        obv = _obv(close, df["volume"])
        mfi = _mfi(high, low, close, df["volume"], 14)
        typical = (high + low + close) / 3
        vol_nonzero = df["volume"].where(df["volume"] > 0)
        vwap = ((typical * vol_nonzero).cumsum() / vol_nonzero.cumsum()).ffill()
        vol_avg = volume.rolling(20).mean()
        vol_ratio = volume.iloc[-1] / vol_avg.iloc[-1] if not math.isnan(vol_avg.iloc[-1]) else math.nan

        prev = df.iloc[-2] if len(df) >= 2 else df.iloc[-1]
        piv = _pivots(prev["high"], prev["low"], prev["close"])

        vals = {
            "close": close.iloc[-1],
            "ema50": ema50.iloc[-1],
            "ema200": ema200.iloc[-1],
            "macd": macd.iloc[-1],
            "macd_sig": macd_sig.iloc[-1],
            "macd_hist": macd_hist.iloc[-1],
            "rsi": rsi.iloc[-1],
            "stoch_k": stoch_k.iloc[-1],
            "stoch_d": stoch_d.iloc[-1],
            "adx": adx.iloc[-1],
            "cci": cci.iloc[-1],
            "atr": atr.iloc[-1],
            "atr_pct": atr_pct.iloc[-1],
            "pct_b": pct_b.iloc[-1],
            "obv": obv.iloc[-1],
            "mfi": mfi.iloc[-1],
            "vwap": vwap.iloc[-1],
            "vol_ratio": vol_ratio,
        }

        bias = _direction_from_values(vals)
        trend = _badge_trend(vals["ema50"], vals["ema200"])
        blocks.append(
            "\n".join(
                [
                    "",
                    f"*{display}* `{yahoo_symbol}` — {label}",
                    f"- Bias: *{bias}* | trend `{trend}`",
                    f"- Price: `{_fmt(vals['close'], 2)}` | VWAP `{_fmt(vals['vwap'], 2)}`",
                    f"- EMA50 `{_fmt(vals['ema50'], 2)}` | EMA200 `{_fmt(vals['ema200'], 2)}`",
                    f"- RSI `{_fmt(vals['rsi'], 1)}` | Stoch K/D `{_fmt(vals['stoch_k'], 1)}` / `{_fmt(vals['stoch_d'], 1)}`",
                    f"- MACD `{_fmt(vals['macd'], 3)}` / Signal `{_fmt(vals['macd_sig'], 3)}` / Hist `{_fmt(vals['macd_hist'], 3)}`",
                    f"- ADX `{_fmt(vals['adx'], 1)}` | CCI `{_fmt(vals['cci'], 1)}`",
                    f"- ATR `{_fmt(vals['atr'], 2)}` (`{_fmt(vals['atr_pct'], 2)}%`) | BB%B `{_fmt(vals['pct_b'], 3)}`",
                    f"- Volume x`{_fmt(vals['vol_ratio'], 2)}` | OBV `{_fmt(vals['obv'], 0)}` | MFI `{_fmt(vals['mfi'], 1)}`",
                    f"- Pivots: P `{_fmt(piv['pivot'], 2)}` | R1 `{_fmt(piv['r1'], 2)}` R2 `{_fmt(piv['r2'], 2)}` | S1 `{_fmt(piv['s1'], 2)}` S2 `{_fmt(piv['s2'], 2)}`",
                ]
            )
        )

    return "\n".join(blocks)
