# services/autopost.py
from __future__ import annotations
import os
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

import asyncio
import math
from services.signal_filter import should_send_signal, MockPriceAPI

from telegram import InlineKeyboardMarkup, InlineKeyboardButton  # ✨ стакан/гайд кнопка
from utils.settings_ob import is_ob_enabled  # ✨ перемикач стакану
from market_data.orderbook_light import get_orderbook_metrics  # ✨ метрики стакану

from utils.settings import get_setting
from utils.db import get_conn
from core_config import CFG  # ✨ для wall_near_pct
from utils.user_settings import get_user_settings

# 🔹 Мінімальний OB-API для «стін» (фолбеково)
try:
    # зроби свій імпорт під свій модуль, якщо відрізняється
    from market_data.orderbook import get_orderbook  # get_orderbook(symbol, limit=1000)->{"bids":[[p,q],...],"asks":[[p,q],...]}
except Exception:
    get_orderbook = None  # type: ignore[misc]

try:
    from services.analyzer_core import compute_indicators, evaluate_gate, compute_rr_metrics  # type: ignore
except Exception:
    compute_indicators = None
    evaluate_gate = None

    def compute_rr_metrics(entry: float, sl: float, tp: Optional[float]):
        rr_eps = 1e-6
        dist = abs(entry - sl)
        rr_t = ((tp - entry) / dist) if (tp is not None and dist > rr_eps) else None
        return {"entry_sl_dist": dist, "rr_target": rr_t}


log = logging.getLogger("autopost")


def _fmt_num(x: float, decimals: int = 2) -> str:
    return f"{x:,.{decimals}f}".replace(",", " ")


def _fmt_price(p: Optional[float]) -> str:
    if p is None:
        return "-"
    decimals = 2 if abs(p) >= 100 else 6
    return _fmt_num(float(p), decimals)


def _fmt_pct(x: Optional[float]) -> str:
    if x is None:
        return "-"
    return f"{float(x):.2f}%"


def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


# >>> SAFE RR: надійні перетворення та обчислення RR
def _safe_float(x):
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except Exception:
        return None


def _compute_rr_num(direction: str, entry: float, stop: float, tp: float) -> Optional[float]:
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


# ───── DB helpers ─────
def _seen_recently(conn, user_id: str, symbol: str, timeframe: str, window_sec: int = 90) -> bool:
    now = _now_ts()
    row = conn.execute(
        "SELECT 1 FROM autopost_log WHERE user_id=? AND symbol=? AND timeframe=? AND ts>=?",
        (user_id, symbol, timeframe, now - window_sec),
    ).fetchone()
    return bool(row)


def mark_autopost_sent(*, symbol: str, timeframe: str, rr: float | None = None, user_id: str | None = None) -> None:
    if user_id is None:
        user_id = get_setting("autopost_user_id", "default") or "default"
    ts = _now_ts()
    with get_conn() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(autopost_log)").fetchall()]
        has_rr = "rr" in cols
        has_ts_sent = "ts_sent" in cols
        if has_rr and has_ts_sent:
            conn.execute(
                "INSERT INTO autopost_log(user_id,symbol,timeframe,rr,ts_sent,ts) VALUES(?,?,?,?,?,?)",
                (user_id, symbol, timeframe, float(rr or 0.0), ts, ts),
            )
        elif has_rr:
            conn.execute(
                "INSERT INTO autopost_log(user_id,symbol,timeframe,ts,rr) VALUES(?,?,?,?,?)",
                (user_id, symbol, timeframe, ts, float(rr or 0.0)),
            )
        else:
            conn.execute(
                "INSERT INTO autopost_log(user_id,symbol,timeframe,ts) VALUES(?,?,?,?)",
                (user_id, symbol, timeframe, ts),
            )
        conn.commit()


# ───── Concurrency-safe reservation in DB (антидубль) ─────
def _reserve_autopost_send(*, user_id: str, symbol: str, timeframe: str, rr: float | None, window_sec: int) -> bool:
    """
    Атомарно резервуємо слот у межах dedup-вікна. Якщо вже є свіжий запис — False.
    Інакше вставляємо рядок із ts=now (і rr, якщо є). Якщо є NOT NULL ts_sent — ставимо його теж (now).
    """
    now = _now_ts()
    with get_conn() as conn:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT 1 FROM autopost_log WHERE user_id=? AND symbol=? AND timeframe=? AND ts>=?",
            (user_id, symbol, timeframe, now - window_sec),
        ).fetchone()
        if row:
            return False

        cols = [r[1] for r in cur.execute("PRAGMA table_info(autopost_log)").fetchall()]
        has_rr = "rr" in cols
        has_ts_sent = "ts_sent" in cols

        if has_rr and has_ts_sent:
            cur.execute(
                "INSERT INTO autopost_log(user_id,symbol,timeframe,ts,rr,ts_sent) VALUES(?,?,?,?,?,?)",
                (user_id, symbol, timeframe, now, float(rr or 0.0), now),
            )
        elif has_ts_sent:
            cur.execute(
                "INSERT INTO autopost_log(user_id,symbol,timeframe,ts,ts_sent) VALUES(?,?,?,?,?)",
                (user_id, symbol, timeframe, now, now),
            )
        elif has_rr:
            cur.execute(
                "INSERT INTO autopost_log(user_id,symbol,timeframe,ts,rr) VALUES(?,?,?,?,?)",
                (user_id, symbol, timeframe, now, float(rr or 0.0)),
            )
        else:
            cur.execute(
                "INSERT INTO autopost_log(user_id,symbol,timeframe,ts) VALUES(?,?,?,?)",
                (user_id, symbol, timeframe, now),
            )

        conn.commit()
        return True



def _complete_autopost_send(*, user_id: str, symbol: str, timeframe: str, rr: float | None) -> None:
    """
    Після успішної відправки ставимо ts_sent (якщо є такий стовпчик) у найсвіжішому рядку.
    Якщо ts_sent-нема — нічого страшного.
    """
    now = _now_ts()
    with get_conn() as conn:
        cur = conn.cursor()
        cols = [r[1] for r in cur.execute("PRAGMA table_info(autopost_log)").fetchall()]
        has_ts_sent = "ts_sent" in cols
        has_rr = "rr" in cols
        if has_ts_sent:
            try:
                cur.execute(
                    """
                    UPDATE autopost_log
                    SET ts_sent=?, rr=COALESCE(?, rr)
                    WHERE rowid IN (
                        SELECT rowid FROM autopost_log
                        WHERE user_id=? AND symbol=? AND timeframe=?
                        ORDER BY ts DESC
                        LIMIT 1
                    )
                    """,
                    (now, float(rr or 0.0) if has_rr else None, user_id, symbol, timeframe),
                )
            except Exception:
                cur.execute(
                    """
                    UPDATE autopost_log
                    SET ts_sent=?
                    WHERE rowid IN (
                        SELECT rowid FROM autopost_log
                        WHERE user_id=? AND symbol=? AND timeframe=?
                        ORDER BY ts DESC
                        LIMIT 1
                    )
                    """,
                    (now, user_id, symbol, timeframe),
                )
            conn.commit()


# ───── Indicators summary (для компактного блоку) ─────
def _ind_summary(direction: str, entry: float, ind: Optional[Dict[str, Any]]) -> Dict[str, Any]:
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
    out.update(
        {
            "trend_ok": trend_ok,
            "atr_pct": atr_pct_val,
            "vwap_pct": vwap_pct,
            "rsi": float(rsi) if rsi is not None else None,
            "ema50": float(ema50) if ema50 is not None else None,
            "ema200": float(ema200) if ema200 is not None else None,
        }
    )
    return out


# ───── preset3 metrics (реюз для панелі та скорингу) ─────
def _compute_preset3_metrics(df):
    """Повертає dict метрик для скорингу та панелі. None якщо немає 'ta' або замало барів."""
    try:
        import numpy as np  # noqa: F401
        from ta.trend import EMAIndicator, SMAIndicator, MACD, ADXIndicator, CCIIndicator
        from ta.momentum import RSIIndicator, StochRSIIndicator, MFIIndicator
        from ta.volatility import AverageTrueRange, BollingerBands
        from ta.volume import OnBalanceVolumeIndicator as _OBV
    except Exception:
        return None
    if df is None or len(df) < 30:
        return None

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    vol = df["volume"].astype(float)

    from ta.trend import EMAIndicator as _EMA
    ema50 = float(_EMA(close, window=50).ema_indicator().iloc[-1])
    ema200 = float(_EMA(close, window=200 if len(close) >= 200 else min(200, len(close))).ema_indicator().iloc[-1])
    from ta.trend import SMAIndicator as _SMA
    sma7 = float(_SMA(close, window=7).sma_indicator().iloc[-1])
    sma25 = float(_SMA(close, window=25).sma_indicator().iloc[-1])

    macd_i = MACD(close)
    macd = float(macd_i.macd().iloc[-1])
    macd_s = float(macd_i.macd_signal().iloc[-1])
    macd_h = float(macd_i.macd_diff().iloc[-1])

    rsi14 = float(RSIIndicator(close, window=14).rsi().iloc[-1])
    stoch = StochRSIIndicator(close, window=14, smooth1=3, smooth2=3)
    st_k = float(stoch.stochrsi_k().iloc[-1])
    st_d = float(stoch.stochrsi_d().iloc[-1])

    adx14 = float(ADXIndicator(high, low, close, window=14).adx().iloc[-1])
    cci20 = float(CCIIndicator(high, low, close, window=20).cci().iloc[-1])

    atr14 = float(AverageTrueRange(high, low, close, window=14).average_true_range().iloc[-1])
    px = float(close.iloc[-1])
    atr_pct = (atr14 / px * 100.0) if px else None

    bb = BollingerBands(close, window=20, window_dev=2)
    bb_pb = float(bb.bollinger_pband().iloc[-1])  # 0..1

    obv = float(_OBV(close, vol).on_balance_volume().iloc[-1])
    mfi14 = float(MFIIndicator(high, low, close, vol, window=14).money_flow_index().iloc[-1])

    v_last = float(vol.iloc[-1])
    v_avg20 = float(vol.tail(20).mean()) if len(vol) >= 20 else float(vol.mean())
    v_ratio = (v_last / v_avg20) if v_avg20 else 1.0

    # Півоти з попередньої свічки
    H = float(high.iloc[-2])
    L = float(low.iloc[-2])
    C = float(close.iloc[-2])
    P = (H + L + C) / 3.0
    R1 = 2 * P - L
    S1 = 2 * P - H
    R2 = P + (H - L)
    S2 = P - (H - L)
    R3 = H + 2 * (P - L)
    S3 = L - 2 * (H - P)

    return {
        "price": px,
        "ema50": ema50,
        "ema200": ema200,
        "sma7": sma7,
        "sma25": sma25,
        "macd": macd,
        "macd_sig": macd_s,
        "macd_hist": macd_h,
        "rsi14": rsi14,
        "st_k": st_k,
        "st_d": st_d,
        "adx14": adx14,
        "cci20": cci20,
        "atr14": atr14,
        "atr_pct": atr_pct,
        "bb_pband": bb_pb,
        "obv": obv,
        "mfi14": mfi14,
        "vol_last": v_last,
        "vol_avg20": v_avg20,
        "vol_ratio": v_ratio,
        "pivot": {"P": P, "R1": R1, "R2": R2, "R3": R3, "S1": S1, "S2": S2, "S3": S3},
    }


# ───── Verbose «preset3» панель ─────
def _build_preset3_panel(df, price_decimals: int = 2, symbol: str = None, sentiment: dict = None) -> Optional[str]:
    m = _compute_preset3_metrics(df)
    if not m:
        return None

    def fp(x: float) -> str:
        dec = 2 if abs(x) >= 100 else 6
        return _fmt_num(x, dec)

    trend_up = "🟢" if m["ema50"] >= m["ema200"] else "🔴"
    macd_sw = "🟢" if m["macd_hist"] >= 0 else "🔴"

    Piv = m["pivot"]
    lines = [
        f"💵 Price: {fp(m['price'])}",
        f"- 📈 Trend: {trend_up} EMA50={fp(m['ema50'])}, EMA200={fp(m['ema200'])} | SMA7={fp(m['sma7'])}, SMA25={fp(m['sma25'])}",
        f"  ↳ EMA50>EMA200 = бичий тренд, нижче = ведмежий",
        f"- 🔁 MACD: {macd_sw} MACD={_fmt_num(m['macd'], 4)}, Signal={_fmt_num(m['macd_sig'], 4)}, Hist={_fmt_num(m['macd_hist'], 4)}",
        f"  ↳ Hist>0 = імпульс вгору, <0 = вниз",
        f"- 🎚 RSI(14): {'🟢' if m['rsi14']>=50 else '🔴'} {_fmt_num(m['rsi14'],1)} | StochRSI %K={_fmt_num(m['st_k'],1)} %D={_fmt_num(m['st_d'],1)}",
        f"  ↳ RSI>70 перекуплено, <30 перепродано",
        f"- 💥 ADX(14): {'💪' if m['adx14']>=20 else '😴'} {_fmt_num(m['adx14'],1)} | CCI(20) {_fmt_num(m['cci20'],1)}",
        f"  ↳ ADX>25 сильний тренд, <20 флет",
        f"- 🌊 ATR(14): {fp(m['atr14'])} ({_fmt_num(m['atr_pct'],3)}%)",
        f"  ↳ Волатильність, базис для SL/TP",
        f"- 🎯 Bollinger %B: {_fmt_num(m['bb_pband'],3)}",
        f"  ↳ <0.2 біля низу, >0.8 біля верху",
        f"- 📦 OBV: {_fmt_num(m['obv'],0)} | MFI(14) {_fmt_num(m['mfi14'],1)}",
        f"  ↳ OBV росте = покупці, MFI>80 перекуплено",
        f"- 📊 Volume: {_fmt_num(m['vol_last'],2)} (x{_fmt_num(m['vol_ratio'],2)} vs 20-bar avg) •",
        f"- 🧭 Pivots: P={fp(Piv['P'])} | R1={fp(Piv['R1'])} R2={fp(Piv['R2'])} R3={fp(Piv['R3'])} | S1={fp(Piv['S1'])} S2={fp(Piv['S2'])} S3={fp(Piv['S3'])}",
    ]
    
    # Додаємо Long/Short Ratio якщо є
    if sentiment:
        ls_emoji = sentiment.get('bias_emoji', '⚖️')
        long_pct = sentiment.get('long_pct', 0)
        short_pct = sentiment.get('short_pct', 0)
        ls_ratio = sentiment.get('ls_ratio', 1)
        lines.append(f"- 📊 L/S Ratio: {ls_emoji} Long {long_pct:.0f}% / Short {short_pct:.0f}% (ratio {ls_ratio:.2f})")
        lines.append(f"  ↳ >1.5 багато лонгів (squeeze?), <0.7 багато шортів")
    
    return "\n".join(lines)


# >>> ДОДАНО: preset3 lite — фолбек без df/ta
def _build_panel_lite(entry: Optional[float], ind_sum: Dict[str, Any]) -> str:
    def fp(x: Optional[float]) -> str:
        if x is None:
            return "-"
        dec = 2 if abs(float(x)) >= 100 else 6
        return _fmt_num(float(x), dec)

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
        f"🌊 ATR%: {_fmt_pct(atr_pct)}",
        f"🎯 VWAPΔ%: {_fmt_pct(vwap_pct)}",
    ]
    return "\n".join(lines)


# ───── (старий) швидкий скоринг (залишаємо) ─────
def _quick_qscore(direction: str, rr_est: Optional[float], df) -> tuple[int, List[str]]:  # noqa: D401
    from ta.trend import EMAIndicator, MACD, ADXIndicator
    from ta.momentum import RSIIndicator
    from ta.volatility import BollingerBands

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    ema50 = EMAIndicator(close, 50).ema_indicator().iloc[-1]
    ema200 = EMAIndicator(close, 200 if len(close) >= 200 else min(200, len(close))).ema_indicator().iloc[-1]
    macd_h = float(MACD(close).macd_diff().iloc[-1])
    rsi14 = float(RSIIndicator(close, 14).rsi().iloc[-1])
    adx14 = float(ADXIndicator(high, low, close, 14).adx().iloc[-1])
    pband = float(BollingerBands(close, 20, 2).bollinger_pband().iloc[-1])

    s = 0
    tags: List[str] = []
    trend_ok = (ema50 >= ema200)
    if (direction == "LONG" and trend_ok) or (direction == "SHORT" and not trend_ok):
        s += 20
        tags.append("trend✓")
    else:
        s -= 5
        tags.append("trend×")

    if (direction == "LONG" and rsi14 >= 55) or (direction == "SHORT" and rsi14 <= 45):
        s += 15
        tags.append("rsi✓")
    elif 45 <= rsi14 <= 55:
        s += 5
        tags.append("rsi~")
    else:
        s -= 10
        tags.append("rsi×")

    if (direction == "LONG" and macd_h > 0) or (direction == "SHORT" and macd_h < 0):
        s += 10
        tags.append("macd✓")
    else:
        s -= 10
        tags.append("macd×")

    if adx14 >= 20:
        s += 10
        tags.append("adx20+")
    elif adx14 >= 15:
        s += 4
        tags.append("adx15+")

    if direction == "LONG":
        if pband < 0.2:
            s += 8
            tags.append("bb_low")
        elif pband < 0.5:
            s += 4
    else:
        if pband > 0.8:
            s += 8
            tags.append("bb_high")
        elif pband > 0.5:
            s += 4

    if rr_est is not None:
        if rr_est >= 1.8:
            s += 10
            tags.append("rr1.8+")
        elif rr_est >= 1.5:
            s += 6
            tags.append("rr1.5+")

    s = max(0, min(100, int(round(s))))
    return s, tags


# --- NEW: простий quality-скоринг на ta ---
def _qscore_basic(dir: str, rr_est: Optional[float], df) -> tuple[int, List[str]]:
    try:
        import numpy as np  # noqa: F401
        from ta.trend import EMAIndicator, MACD, ADXIndicator
        from ta.momentum import RSIIndicator
        from ta.volatility import BollingerBands
    except Exception:
        return (0, ["no-ta"])

    if df is None or len(df) < 30:
        return (0, ["no-df"])

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    ema50 = EMAIndicator(close, 50).ema_indicator().iloc[-1]
    ema200 = EMAIndicator(close, (200 if len(close) >= 200 else min(200, len(close)))).ema_indicator().iloc[-1]
    macd = MACD(close).macd_diff().iloc[-1]
    rsi14 = RSIIndicator(close, 14).rsi().iloc[-1]
    adx14 = ADXIndicator(high, low, close, 14).adx().iloc[-1]
    pband = BollingerBands(close, 20, 2).bollinger_pband().iloc[-1]  # 0..1

    trend_up = float(ema50) >= float(ema200)
    s = 0
    tags: List[str] = []

    if (dir == "LONG" and trend_up) or (dir == "SHORT" and not trend_up):
        s += 20
        tags.append("trend✓")
    else:
        tags.append("trend×")

    if (dir == "LONG" and rsi14 >= 55) or (dir == "SHORT" and rsi14 <= 45):
        s += 15
        tags.append("rsi✓")
    elif 45 <= rsi14 <= 55:
        s += 5
        tags.append("rsi~")
    else:
        s -= 10
        tags.append("rsi×")

    if (dir == "LONG" and macd < 0) or (dir == "SHORT" and macd > 0):
        s -= 10
        tags.append("macd×")
    else:
        s += 10
        tags.append("macd✓")

    if adx14 >= 20:
        s += 10
        tags.append("adx20+")
    elif adx14 >= 15:
        s += 4
        tags.append("adx15+")

    if dir == "LONG":
        if pband < 0.2:
            s += 8
            tags.append("bb_low")
        elif pband < 0.5:
            s += 4
    else:
        if pband > 0.8:
            s += 8
            tags.append("bb_high")
        elif pband > 0.5:
            s += 4

    if rr_est is not None:
        if rr_est >= 1.8:
            s += 10
            tags.append("rr1.8+")
        elif rr_est >= 1.5:
            s += 6
            tags.append("rr1.5+")

    return (max(0, min(100, int(round(s)))), tags)


# --- NEW: акуратне збереження сигналу в БД для KPI ---
def _persist_signal(cur, msg: Dict[str, Any]) -> int:
    cols = [r[1] for r in cur.execute("PRAGMA table_info(signals)")]
    now = _now_ts()
    snap = None
    try:
        df = msg.get("df")
        if df is not None:
            snap = int(df.iloc[-1]["ts"])
    except Exception:
        pass

    payload = {
        "user_id": int(msg.get("chat_id") or get_setting("autopost_user_id", os.getenv("TELEGRAM_CHAT_ID", "1126438536"))),
        "symbol": msg["symbol"],
        "timeframe": msg["timeframe"],
        "direction": msg["direction"],
        "entry": float(msg["entry"]),
        "sl": float(msg["sl"]),
        "tp": (float(msg["tp"]) if msg.get("tp") is not None else None),
        "rr": float(msg.get("rr") or 0.0),
        "source": "autopost",
        "status": "SUGGESTED",
        "snapshot_ts": snap or now,
        "size_usd": 100.0,
        "ts_created": now,
        "details": json.dumps(
            {
                "ind": msg.get("ind"),
                "gate": {"score": msg.get("gate_score"), "total": msg.get("gate_total")},
                "reasons": msg.get("reasons"),
                "quality": {"score": msg.get("qscore"), "tags": msg.get("qtags")},
                "orderbook": msg.get("ob"),
            },
            ensure_ascii=False,
        ),
    }

    keep = [k for k in payload if k in cols]
    sql = f"INSERT INTO signals({','.join(keep)}) VALUES({','.join(['?']*len(keep))})"
    cur.execute(sql, tuple(payload[k] for k in keep))
    return cur.lastrowid


# ───── Gate (RR + індикаторний шлюз) ─────
def _gate_ok(candidate: Dict[str, Any], rr_target: Optional[float]) -> tuple[bool, str]:
    min_entry_rr = float(get_setting("min_entry_rr", get_setting("autopost_min_rr", "1.5")) or 1.5)
    if rr_target is None or float(rr_target) < min_entry_rr:
        return (False, "low_rr")
    gate_enabled = str(get_setting("indicator_gate_enabled", "false")).lower() == "true"
    if not gate_enabled or compute_indicators is None:
        return (True, "")
    df = candidate.get("df")
    if df is None:
        return (True, "")
    cfg = {
        "ATR_MIN": float(get_setting("atr_min", "0.004") or 0.004),
        "VWAP_DIST_MIN": float(get_setting("vwap_dist_min", "0.0015") or 0.0015),
        "TREND_FILTER": get_setting("trend_filter", "ema50_over_ema200"),
    }
    direction = candidate.get("direction", "LONG")
    try:
        indicators = compute_indicators(df, cfg)  # type: ignore[misc]
        g = evaluate_gate(indicators, direction, cfg)  # type: ignore[misc]
        min_pass = int(get_setting("indicator_min_pass", "8") or 8)
        if g.get("score", 0) < min_pass:
            return (False, "low_score")
    except Exception as e:
        log.warning("[autopost] indicators/gate failed: %s", e)
        return (True, "")
    return (True, "")


# 🔹 Мінімальний помічник для «стін»
def _best_walls(ob, last_price: float, win_pct: float = 1.0, min_quote_usd: float = 50000):
    """Повертає найближчу суттєву bid/ask стіну у ±win_pct% від ціни."""
    if not ob or not ob.get("bids") or not ob.get("asks") or not last_price:
        return None, None
    lo, hi = last_price * (1 - win_pct / 100), last_price * (1 + win_pct / 100)

    def pick(side: str):
        cand = []
        for p, q in ob.get(side) or []:
            try:
                p, q = float(p), float(q)
            except Exception:
                continue
            if not (lo <= p <= hi):
                continue
            quote = p * q
            if quote >= min_quote_usd:
                dist = abs(p - last_price)
                cand.append((dist, p, q, quote))
        if not cand:
            return None
        cand.sort(key=lambda x: x[0])
        _d, p, q, quote = cand[0]
        return {"price": p, "qty": q, "quote": quote}

    return pick("bids"), pick("asks")


# ───── Форматер повідомлення (панель + Walls/Imbalance) ─────
def _format_message_text(
    symbol: str,
    direction: str,
    timeframe: str,
    entry: float,
    sl: float,
    tp: Optional[float],
    rr_t: Optional[float],
    ind_sum: Dict[str, Any],
    gate_score: Optional[int],
    gate_total: Optional[int],
    panel: Optional[str] = None,
    reasons: Optional[List[str]] = None,
    qscore: Optional[int] = None,
    qtags: Optional[List[str]] = None,
    ob: Optional[Dict[str, Any]] = None,  # ✨ стакан (розширені метрики)
    ob_extra_lines: Optional[List[str]] = None,  # 🔹 мінімальні «стіни» (bid/ask)
) -> str:
    rr_text = "-" if rr_t is None else f"{float(rr_t):.2f}"
    header = f"📈 {symbol} · {timeframe} · {direction}  |  RR {rr_text}"
    e_txt = _fmt_price(entry)
    s_txt = _fmt_price(sl)
    t_txt = "-" if tp is None else _fmt_price(tp)

    atr_min_pct = float(get_setting("autopost_min_atr_pct", "0") or 0.0)
    vwap_min_pct = float(get_setting("vwap_dist_min", "0") or 0.0)

    if direction == "LONG":
        rsi_thr = float(get_setting("rsi_long_min", "50") or 50)
        rsi_cmp = f">={int(rsi_thr)}?"
        rsi_ok = (ind_sum.get("rsi") is not None and ind_sum["rsi"] >= rsi_thr)
    else:
        rsi_thr = float(get_setting("rsi_short_max", "50") or 50)
        rsi_cmp = f"<={int(rsi_thr)}?"
        rsi_ok = (ind_sum.get("rsi") is not None and ind_sum["rsi"] <= rsi_thr)

    trend_flag = ind_sum.get("trend_ok")
    atr_pct = ind_sum.get("atr_pct")
    vwap_pct = ind_sum.get("vwap_pct")

    atr_ok = None if atr_pct is None else (atr_pct >= atr_min_pct)
    vwap_ok = None if vwap_pct is None else ((vwap_pct >= vwap_min_pct) if vwap_min_pct > 0 else True)

    rsi_val = "-" if ind_sum.get("rsi") is None else f"{ind_sum['rsi']:.1f}"

    # Гарантуємо, що qtags — список
    if isinstance(qtags, str):
        qtags = qtags.split()

    lines = [
        header,
        f"E {e_txt}  •  SL {s_txt}  •  TP {t_txt}",
        "",
        "🧭 Тренд: EMA50≥EMA200 " + ("✅" if trend_flag is True else ("❌" if trend_flag is False else "–")),
        "📊 ATR: " + _fmt_pct(atr_pct) + (" ✅" if atr_ok is True else (" ❌" if atr_ok is False else " –")),
        "🎯 VWAPΔ: " + _fmt_pct(vwap_pct) + (" ✅" if vwap_ok is True else (" ❌" if vwap_ok is False else " –")),
        f"💪 RSI14: {rsi_val} ({rsi_cmp}) " + ("✅" if rsi_ok else ("❌" if ind_sum.get('rsi') is not None else "–")),
        f"Gate: {'-' if (gate_score is None or gate_total is None) else f'{gate_score}/{gate_total}'}",
    ]

    # Reasons блок (якщо передали)
    if reasons and isinstance(reasons, list) and len(reasons) > 0:
        lines += ["", "🧠 Reasons:"]
        for r in reasons[:6]:
            lines.append(f"• {r}")

    # ВАЖЛИВО: панель одразу після Reasons
    if panel:
        lines += ["", panel]

    # >>> Розширені метрики стакану (orderbook_light)
    if ob:
        sup = ob.get("support_wall")
        res = ob.get("resistance_wall")
        wall_line = "🧱 Walls: "
        parts = []
        if sup:
            parts.append(f"support @{sup['price']:.2f} ≈ {sup['vol_str']} ({sup['dist_str']})")
        if res:
            parts.append(f"resistance @{res['price']:.2f} ≈ {res['vol_str']} ({res['dist_str']})")
        if parts:
            wall_line += " • ".join(parts)
            lines.append(wall_line)
        if ob.get("imbalance") is not None:
            lines.append(f"Imbalance: {ob['imbalance']:.2f}")

    # 🔹 Мінімальні «стіни» (bid/ask) — простий блок
    if ob_extra_lines:
        lines += ["", "OrderBook (увімкнено):"]
        lines += ["• " + s for s in ob_extra_lines]

    # Quality блок (якщо передали)
    if qscore is not None:
        tags_str = " ".join(qtags or [])
        lines += ["", f"⭐ Quality: {qscore}/100  ({tags_str})"]

    lines.append(f"ts: {_now_ts()}")
    return "\n".join(lines)


def _parse_rr_from_reasons(reasons) -> Optional[float]:
    if not reasons:
        return None
    for r in reasons:
        if "RR=" in r:
            try:
                return float(r.split("RR=")[-1])
            except Exception:
                pass
    return None


# ───── Основний цикл автопосту ─────
# Delegated to services.autopost.core
from services.autopost.core import run_autopost_once

# Legacy function - now delegated
async def _legacy_run_autopost_once(application=None) -> List[Dict[str, Any]]:
    """
    Якщо application передано (telegram.ext.Application), функція САМА відправить повідомлення у чат
    з клавіатурою, включно з кнопкою «📘 Гайд до цього сигналу».
    Якщо application=None — просто поверне список prepared повідомлень для зовнішньої відправки.
    """
    try:
        from services.autopost_sources import collect_autopost_candidates  # type: ignore
        candidates = collect_autopost_candidates()
    except Exception:
        log.info("[autopost] no autopost_sources.collect_autopost_candidates(), nothing to send")
        return []
    if not candidates:
        return []

    default_chat = os.getenv("TELEGRAM_CHAT_ID", "1126438536")
    user_id = os.getenv("TELEGRAM_CHAT_ID", "1126438536")
    dedup_sec = int(get_setting("dedup_window_sec", "90") or 90)

    # перемикач панелі
    preset = (get_setting("indicator_preset", os.getenv("INDICATOR_PRESET", "")) or "").lower()
    want_panel = (preset == "preset3") or (str(get_setting("autopost_panel_verbose", "false")).lower() == "true")

    log.debug("[autopost] want_panel=%s preset=%s verbose=%s", want_panel, preset, str(get_setting("autopost_panel_verbose", "false")))

    # quality налаштування
    quality_on = str(get_setting("quality_select_enabled", "false")).lower() == "true"
    quality_min = float(get_setting("quality_min", "50") or 50.0)
    quality_topk = int(get_setting("quality_top_k", "3") or 3)

    prepared: List[Dict[str, Any]] = []

    # in-run dedup (клон-кандидати в одному проході)
    seen_keys: Set[Tuple[str, str]] = set()

    price_api = MockPriceAPI()  # TODO: замінити на реальний API для продакшн
    with get_conn() as conn:
        for c in candidates:
            try:
                symbol = str(c["symbol"]).upper()
                direction = str(c.get("direction", "LONG")).upper()
                timeframe = str(c.get("timeframe", "1h")).lower()
                entry = float(c["entry"])
                sl = float(c["sl"])
                tp = c.get("tp")
                tp = float(tp) if tp is not None else None
                chat_id = c.get("chat_id") or default_chat
                if not chat_id:
                    log.warning("[autopost] skip %s/%s: chat_id is empty", symbol, timeframe)
                    continue

                # --- ФІЛЬТРАЦІЯ СИГНАЛУ ---
                signal = {
                    "symbol": symbol,
                    "direction": direction,
                    "tf": timeframe,
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "rr": c.get("rr") or c.get("rr_target") or 0.0,
                }
                us = get_user_settings(user_id) if user_id else {}
                ok, reason = should_send_signal(signal, us, price_api)
                if not ok:
                    log.info(f"[autopost] SKIP {symbol}/{timeframe}: {reason}")
                    continue

                # in-run ключ
                key = (symbol, timeframe)
                if key in seen_keys:
                    log.info("[autopost] in-run dedup %s/%s — skip", symbol, timeframe)
                    continue
                seen_keys.add(key)

                # дедуп по БД (вікно)
                if _seen_recently(conn, user_id, symbol, timeframe, window_sec=dedup_sec):
                    log.info("[autopost] dedup_recent %s/%s — skip", symbol, timeframe)
                    continue

                # індикатори (мінімум для компактного блоку)
                ind_src: Optional[Dict[str, Any]] = c.get("ind")
                ind_sum = _ind_summary(direction, entry, ind_src)

                # панель preset3
                panel_text = None
                df = c.get("df")
                if want_panel:
                    try:
                        if df is None or (hasattr(df, "__len__") and len(df) < 30):
                            log.debug("[autopost] no/short df for %s/%s: df=%s", symbol, timeframe, (type(df).__name__ if df is not None else None))
                        panel_text = _build_preset3_panel(df)
                        if not panel_text:
                            panel_text = _build_panel_lite(entry, ind_sum)  # фолбек
                    except Exception as e:
                        log.debug("[autopost] panel build failed for %s/%s: %s", symbol, timeframe, e)
                        panel_text = _build_panel_lite(entry, ind_sum)

                # quality (basic)
                qscore: Optional[int] = None
                qtags: Optional[List[str]] = None
                rr_est = _parse_rr_from_reasons(c.get("reasons")) or rr_t
                if quality_on:
                    qs, tags = _qscore_basic(direction, rr_est, df)
                    qscore, qtags = qs, tags

                # >>> ДОДАНО: логіка стакану ПІСЛЯ розрахунку індикаторів/rr/quality
                ob = None
                if is_ob_enabled():
                    try:
                        ob = await get_orderbook_metrics(symbol)  # {imbalance, support_wall, resistance_wall}
                        if ob:
                            if qscore is not None:
                                if direction == "LONG" and ob.get("support_wall"):
                                    qscore += 8
                                if direction == "SHORT" and ob.get("resistance_wall"):
                                    qscore += 8
                            near_res = ob.get("resistance_wall")
                            near_sup = ob.get("support_wall")
                            wall_near_pct = float(CFG.get("wall_near_pct", 1.0) or 1.0)
                            if direction == "LONG" and near_res and tp:
                                try:
                                    if abs((float(near_res["price"]) - float(entry)) / float(entry)) * 100 <= wall_near_pct:
                                        if qscore is not None:
                                            qscore -= 6
                                        if rr_t:
                                            rr_t = float(rr_t) * 0.95
                                except Exception:
                                    pass
                            if direction == "SHORT" and near_sup and tp:
                                try:
                                    if abs((float(near_sup["price"]) - float(entry)) / float(entry)) * 100 <= wall_near_pct:
                                        if qscore is not None:
                                            qscore -= 6
                                        if rr_t:
                                            rr_t = float(rr_t) * 0.95
                                except Exception:
                                    pass
                    except Exception:
                        pass

                # 🔹 Мінімальний OrderBook: bid/ask «стіни»
                ob_extra_lines: Optional[List[str]] = None
                try:
                    if get_orderbook is not None:
                        raw_ob = get_orderbook(symbol, limit=int(CFG.get("orderbook_depth_limit", 1000)))
                        last_px = entry
                        if (not last_px) and df is not None:
                            try:
                                last_px = float(df["close"].iloc[-1])
                            except Exception:
                                pass
                        bid_wall, ask_wall = _best_walls(
                            raw_ob,
                            last_price=last_px,
                            win_pct=float(CFG.get("orderbook_window_pct", 1.0)),
                            min_quote_usd=float(CFG.get("orderbook_min_quote_usd", 50000)),
                        )
                        lines = []
                        if bid_wall:
                            lines.append(f"🧱 Bid wall: {bid_wall['price']:.2f} ({bid_wall['quote']:.0f} USDT)")
                        if ask_wall:
                            lines.append(f"🧱 Ask wall: {ask_wall['price']:.2f} ({ask_wall['quote']:.0f} USDT)")
                        if lines:
                            ob_extra_lines = lines
                except Exception as e:
                    log.warning("orderbook walls failed: %s", e)

                # 🔐 Конкурентно-безпечний резерв у БД перед додаванням у prepared/відправкою
                if not _reserve_autopost_send(
                    user_id=user_id,
                    symbol=symbol,
                    timeframe=timeframe,
                    rr=rr_t,
                    window_sec=dedup_sec,
                ):
                    log.info("[autopost] race-dedup %s/%s — already reserved, skip", symbol, timeframe)
                    continue

                # текст
                text = _format_message_text(
                    symbol,
                    direction,
                    timeframe,
                    entry,
                    sl,
                    tp,
                    rr_t,
                    ind_sum,
                    gate_score=c.get("gate_score"),
                    gate_total=c.get("gate_total"),
                    panel=panel_text,
                    reasons=c.get("reasons"),
                    qscore=(qscore if quality_on else None),
                    qtags=(qtags if quality_on else None),
                    ob=ob,  # ✨ розширені метрики (optional)
                    ob_extra_lines=ob_extra_lines,  # 🔹 мінімальні «стіни»
                )

                # готуємо payload
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
                    "ind": ind_src,
                    "gate_score": c.get("gate_score"),
                    "gate_total": c.get("gate_total"),
                    "reasons": c.get("reasons"),
                    "qscore": (qscore if quality_on else None),
                    "qtags": (qtags if quality_on else None),
                    "ob": ob,  # ✨ лог OB
                }
                if "df" in c:
                    msg["df"] = c["df"]

                prepared.append(msg)

            except Exception as e:
                log.warning("[autopost] bad candidate skipped: %s", e)

    # quality фільтрація (після збирання)
    if quality_on and prepared:
        keep = [m for m in prepared if (m.get("qscore") or 0) >= quality_min]
        keep.sort(key=lambda m: (m.get("qscore") or 0), reverse=True)
        prepared = keep[:quality_topk]

    # автозбереження сигналів для KPI
    if prepared:
        with get_conn() as conn:
            cur = conn.cursor()
            for m in prepared:
                try:
                    _persist_signal(cur, m)
                except Exception as e:
                    log.warning("[autopost] persist_signal fail: %s", e)
            conn.commit()

    log.info("[autopost] prepared %d message(s)", len(prepared))

    # >>> Якщо application передано — відправляємо прямо тут з кнопкою «Гайд»
    if application is not None and hasattr(application, "bot") and prepared:
        bot = application.bot
        for m in prepared:
            try:
                # будуємо клавіатуру з опису buttons
                rows: List[List[InlineKeyboardButton]] = []
                for row in m.get("buttons", []):
                    btn_row: List[InlineKeyboardButton] = []
                    for btn in row:
                        if btn.get("type") == "url":
                            btn_row.append(InlineKeyboardButton(btn.get("text", "Open"), url=btn.get("url")))
                        elif btn.get("type") == "cb":
                            btn_row.append(InlineKeyboardButton(btn.get("text", "…"), callback_data=btn.get("data")))
                    if btn_row:
                        rows.append(btn_row)

                # гарантуємо «📘 Гайд» (на випадок модифікацій зверху)
                has_guide = any(any((isinstance(b, InlineKeyboardButton) and b.callback_data == "guide:signal") for b in r) for r in rows)
                if not has_guide:
                    rows.append([InlineKeyboardButton("📘 Гайд до цього сигналу", callback_data="guide:signal")])

                kb = InlineKeyboardMarkup(rows)
                await bot.send_message(chat_id=m["chat_id"], text=m["text"], reply_markup=kb, disable_web_page_preview=True)

                # після успішної відправки — завершуємо резерв (ставимо ts_sent)
                try:
                    _complete_autopost_send(
                        user_id=(get_setting("autopost_user_id", "default") or "default"),
                        symbol=m["symbol"],
                        timeframe=m["timeframe"],
                        rr=(m.get("rr") or 0.0),
                    )
                except Exception:
                    pass

            except Exception as e:
                log.warning("[autopost] send fail: %s", e)

    return prepared
