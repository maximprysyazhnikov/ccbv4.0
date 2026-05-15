"""Top handlers (/top, gainers/volume)."""
from __future__ import annotations

import asyncio
import logging
from typing import List, Tuple
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from market_data.binance_rank import get_all_usdt_24h, get_top_by_quote_volume_usdt
from telegram_bot.handlers.helpers import _send, _chunk

log = logging.getLogger("tg.handlers")

TOP_MODE_VOLUME = "volume"
TOP_MODE_GAINERS = "gainers"


def _build_top_text(rows: List[dict]) -> Tuple[str, List[str]]:
    """Build top list text."""
    def fmt_vol(usdt: float) -> str:
        a = abs(usdt)
        if a >= 1_000_000_000:
            return f"{usdt/1_000_000_000:.2f}B"
        if a >= 1_000_000:
            return f"{usdt/1_000_000:.1f}M"
        if a >= 1_000:
            return f"{usdt/1_000:.1f}K"
        return f"{usdt:.0f}"
    
    lines, symbols = [], []
    lines.append("_Symbol | Price | 24h% | QuoteVol_\n")
    for i, r in enumerate(rows, 1):
        sym = r["symbol"]
        symbols.append(sym)
        price = r["lastPrice"]
        chg = r["priceChangePercent"]
        vol = r["quoteVolume"]
        emoji = "🟢" if chg >= 0 else "🔴"
        lines.append(f"{i:>2}. `{sym}` | `{price:,.6f}` | {emoji} `{chg:+.2f}%` | `{fmt_vol(vol)}`")
    return "\n".join(lines), symbols


def _top_mode_buttons(active: str) -> list[list[InlineKeyboardButton]]:
    """Get top mode toggle buttons."""
    vol = InlineKeyboardButton(
        ("✅ Volume" if active == TOP_MODE_VOLUME else "Volume"),
        callback_data="topmode:volume"
    )
    gai = InlineKeyboardButton(
        ("✅ Gainers" if active == TOP_MODE_GAINERS else "Gainers"),
        callback_data="topmode:gainers"
    )
    return [[vol, gai]]


async def _send_top(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str):
    """Send top list."""
    if mode == TOP_MODE_GAINERS:
        all_rows = await asyncio.to_thread(get_all_usdt_24h)
        all_rows.sort(key=lambda x: x["priceChangePercent"], reverse=True)
        rows = all_rows[:20]
        header = "🏆 *Топ-20 USDT пар — Gainers (24h %)*\n"
    else:
        rows = await asyncio.to_thread(get_top_by_quote_volume_usdt, 20)
        header = "🏆 *Топ-20 USDT пар — Volume (24h QuoteVol)*\n"
    
    text_body, symbols = _build_top_text(rows)
    sym_rows = [
        [InlineKeyboardButton(text=s, callback_data=f"sym:{s}") for s in chunk]
        for chunk in _chunk(symbols, 4)
    ]
    kb = InlineKeyboardMarkup(sym_rows + _top_mode_buttons(mode))
    await _send(update, context, (header + text_body)[:4000], parse_mode="Markdown", reply_markup=kb)


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /top command."""
    mode = TOP_MODE_GAINERS if (context.args and context.args[0].lower().startswith("gain")) else TOP_MODE_VOLUME
    await _send_top(update, context, mode)


async def on_cb_sym(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle symbol selection callback."""
    q = update.callback_query
    await q.answer()
    data = (q.data or "")
    if not data.startswith("sym:"):
        return
    sym = data.split(":", 1)[1].upper()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🤖 AI {sym}", callback_data=f"ai:{sym}")],
        [InlineKeyboardButton(f"📈 Індикатори {sym}", callback_data=f"indic:{sym}")],
        [InlineKeyboardButton(f"🔗 Залежність BTC/ETH {sym}", callback_data=f"dep:{sym}")],
        [InlineKeyboardButton(f"📊 L/S Ratio {sym}", callback_data=f"ls:{sym}")],
    ])
    await _send(update, context, f"Вибери дію для `{sym}`:", parse_mode="Markdown", reply_markup=kb)


async def on_cb_topmode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle top mode toggle callback."""
    q = update.callback_query
    await q.answer()
    data = (q.data or "")
    if not data.startswith("topmode:"):
        return
    mode = data.split(":", 1)[1]
    if mode not in (TOP_MODE_VOLUME, TOP_MODE_GAINERS):
        mode = TOP_MODE_VOLUME
    await _send_top(update, context, mode)
