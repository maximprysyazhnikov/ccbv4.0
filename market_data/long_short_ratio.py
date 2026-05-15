# market_data/long_short_ratio.py
"""
Binance Futures Long/Short Ratio API
Показує співвідношення лонгів і шортів на ринку.
"""
from __future__ import annotations

import logging
import httpx
from typing import Optional
from dataclasses import dataclass

log = logging.getLogger(__name__)

BASE_URL = "https://fapi.binance.com"


@dataclass
class LongShortData:
    """Дані про Long/Short співвідношення."""
    symbol: str
    long_ratio: float      # % лонгів (0-100)
    short_ratio: float     # % шортів (0-100)
    long_short_ratio: float  # longAccount / shortAccount
    top_long_ratio: float  # % топ-трейдерів в лонгах
    top_short_ratio: float # % топ-трейдерів в шортах
    open_interest: float   # Відкритий інтерес (кількість контрактів)
    open_interest_value: float  # Відкритий інтерес в USDT


async def get_global_long_short_ratio(symbol: str = "BTCUSDT", period: str = "5m") -> Optional[dict]:
    """
    Отримати глобальне співвідношення Long/Short по всіх акаунтах.
    period: 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d
    """
    url = f"{BASE_URL}/futures/data/globalLongShortAccountRatio"
    params = {"symbol": symbol, "period": period, "limit": 1}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
            if data:
                return data[0]  # Останній запис
    except Exception as e:
        log.warning("globalLongShortRatio error: %s", e)
    return None


async def get_top_long_short_ratio(symbol: str = "BTCUSDT", period: str = "5m") -> Optional[dict]:
    """
    Співвідношення Long/Short серед топ-трейдерів (по акаунтах).
    """
    url = f"{BASE_URL}/futures/data/topLongShortAccountRatio"
    params = {"symbol": symbol, "period": period, "limit": 1}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
            if data:
                return data[0]
    except Exception as e:
        log.warning("topLongShortRatio error: %s", e)
    return None


async def get_open_interest(symbol: str = "BTCUSDT") -> Optional[dict]:
    """
    Відкритий інтерес - загальна кількість відкритих позицій.
    """
    url = f"{BASE_URL}/fapi/v1/openInterest"
    params = {"symbol": symbol}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        log.warning("openInterest error: %s", e)
    return None


async def get_full_sentiment(symbol: str = "BTCUSDT", period: str = "5m") -> Optional[LongShortData]:
    """
    Отримати повну картину сентименту ринку.
    """
    global_ls = await get_global_long_short_ratio(symbol, period)
    top_ls = await get_top_long_short_ratio(symbol, period)
    oi = await get_open_interest(symbol)
    
    if not global_ls:
        return None
    
    try:
        long_ratio = float(global_ls.get("longAccount", 0)) * 100
        short_ratio = float(global_ls.get("shortAccount", 0)) * 100
        ls_ratio = float(global_ls.get("longShortRatio", 1))
        
        top_long = float(top_ls.get("longAccount", 0)) * 100 if top_ls else 0
        top_short = float(top_ls.get("shortAccount", 0)) * 100 if top_ls else 0
        
        oi_qty = float(oi.get("openInterest", 0)) if oi else 0
        # Приблизна вартість OI (потрібна ціна)
        oi_value = 0
        
        return LongShortData(
            symbol=symbol,
            long_ratio=long_ratio,
            short_ratio=short_ratio,
            long_short_ratio=ls_ratio,
            top_long_ratio=top_long,
            top_short_ratio=top_short,
            open_interest=oi_qty,
            open_interest_value=oi_value,
        )
    except Exception as e:
        log.warning("get_full_sentiment parse error: %s", e)
        return None


async def get_sentiment_short(symbol: str = "BTCUSDT", period: str = "5m") -> Optional[dict]:
    """
    Швидка версія - повертає dict з основними даними для autopost.
    """
    global_ls = await get_global_long_short_ratio(symbol, period)
    if not global_ls:
        return None
    
    try:
        long_ratio = float(global_ls.get("longAccount", 0)) * 100
        short_ratio = float(global_ls.get("shortAccount", 0)) * 100
        ls_ratio = float(global_ls.get("longShortRatio", 1))
        
        # Визначаємо домінуючу сторону
        if long_ratio > short_ratio + 5:
            bias = "LONG"
            bias_emoji = "🟢"
        elif short_ratio > long_ratio + 5:
            bias = "SHORT"
            bias_emoji = "🔴"
        else:
            bias = "NEUTRAL"
            bias_emoji = "⚖️"
        
        return {
            "long_pct": long_ratio,
            "short_pct": short_ratio,
            "ls_ratio": ls_ratio,
            "bias": bias,
            "bias_emoji": bias_emoji,
        }
    except Exception as e:
        log.warning("get_sentiment_short error: %s", e)
        return None


def format_sentiment_text(data: LongShortData) -> str:
    """Форматує дані сентименту для відображення."""
    # Визначаємо домінуючу сторону
    if data.long_ratio > data.short_ratio + 5:
        bias = "🟢 LONG домінує"
    elif data.short_ratio > data.long_ratio + 5:
        bias = "🔴 SHORT домінує"
    else:
        bias = "⚖️ Баланс"
    
    # Форматуємо OI
    oi_str = f"{data.open_interest:,.0f}"
    if data.open_interest > 1_000_000:
        oi_str = f"{data.open_interest/1_000_000:.2f}M"
    elif data.open_interest > 1_000:
        oi_str = f"{data.open_interest/1_000:.1f}K"
    
    return (
        f"📊 *{data.symbol} Sentiment*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🌍 *Всі трейдери:*\n"
        f"   🟢 Long:  {data.long_ratio:.1f}%\n"
        f"   🔴 Short: {data.short_ratio:.1f}%\n"
        f"   📈 L/S Ratio: {data.long_short_ratio:.2f}\n"
        f"\n"
        f"👑 *Топ трейдери:*\n"
        f"   🟢 Long:  {data.top_long_ratio:.1f}%\n"
        f"   🔴 Short: {data.top_short_ratio:.1f}%\n"
        f"\n"
        f"📦 Open Interest: {oi_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{bias}"
    )


def format_sentiment_line(data: dict) -> str:
    """Форматує короткий рядок для autopost."""
    if not data:
        return ""
    return (
        f"📊 L/S: {data['bias_emoji']} {data['long_pct']:.0f}%/{data['short_pct']:.0f}% "
        f"(ratio {data['ls_ratio']:.2f})"
    )


# ───────────────────────────────────────────────
# Синхронна версія для швидкого тесту
# ───────────────────────────────────────────────
def get_sentiment_sync(symbol: str = "BTCUSDT", period: str = "5m") -> Optional[str]:
    """Синхронна версія - повертає готовий текст."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, get_full_sentiment(symbol, period))
                data = future.result(timeout=15)
        else:
            data = asyncio.run(get_full_sentiment(symbol, period))
        
        if data:
            return format_sentiment_text(data)
        return f"⚠️ Не вдалося отримати дані для {symbol}"
    except Exception as e:
        return f"⚠️ Помилка: {e}"
