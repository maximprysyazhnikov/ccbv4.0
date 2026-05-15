"""Shared helper functions for handlers."""
from __future__ import annotations

import os
import logging
import re
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from core_config import CFG
from router.analyzer_router import pick_route

log = logging.getLogger("tg.handlers")


def _llm_allowed() -> bool:
    """Check if LLM is enabled."""
    try:
        if str(os.getenv("LLM_DISABLED", "0")).lower() in ("1", "true", "yes", "on"):
            return False
    except Exception:
        pass
    try:
        import sitecustomize  # noqa: F401
        if getattr(sitecustomize, "LLM_DISABLED", False):
            return False
    except Exception:
        pass
    return True


def _extract_llm_text(resp) -> str | None:
    """Extract text from LLM response."""
    try:
        if not resp:
            return None
        if isinstance(resp, str):
            s = resp.strip()
            return s or None
        if isinstance(resp, dict):
            if isinstance(resp.get("content"), str):
                s = resp["content"].strip()
                return s or None
            choices = resp.get("choices") or resp.get("data")
            if isinstance(choices, list) and choices:
                c0 = choices[0] or {}
                msg = c0.get("message") or {}
                s = (msg.get("content") or c0.get("text") or "").strip()
                return s or None
    except Exception:
        return None
    return None


def _current_ai_model() -> str:
    """Get current AI model name."""
    try:
        probe = (CFG["symbols"][0] if CFG["symbols"] else "BTCUSDT")
        route = pick_route(probe)
        return route.model if route else "unknown"
    except Exception:
        return "unknown"


def _looks_like_symbol(s: str) -> bool:
    """Check if string looks like a trading symbol."""
    s = (s or "").strip().upper()
    if not (2 <= len(s) <= 20):
        return False
    if not all(c.isalnum() for c in s):
        return False
    for q in ("USDT", "FDUSD", "USDC", "BUSD", "BTC", "ETH", "EUR", "TRY"):
        if s.endswith(q):
            return True
    return False


def _pick_default_symbol() -> str:
    """Pick default symbol from config."""
    try:
        for x in CFG["symbols"]:
            x = (x or "").strip().upper()
            if _looks_like_symbol(x):
                return x
    except Exception:
        pass
    return "BTCUSDT"


def _strip_md(s: str) -> str:
    """Strip markdown formatting."""
    _MD_RX = re.compile(r"[*`]|(?<!`)_(?!`)")
    s = _MD_RX.sub("", s or "")
    s = re.sub(r"[^\S\r\n]+", " ", s).strip()
    return s


def _chunk(lst: list, n: int) -> list:
    """Split list into chunks of size n."""
    return [lst[i:i+n] for i in range(0, len(lst), n)]


def _fmt_or_dash(v) -> str:
    """Format value or return dash."""
    try:
        return f"{float(v):.2f}"
    except Exception:
        return "-"


def _safe_float(x) -> Optional[float]:
    """Safely convert to float."""
    try:
        v = float(x)
        if v != v:  # NaN
            return None
        return v
    except Exception:
        return None


async def _send(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str,
                *, parse_mode: Optional[str] = None, reply_markup=None) -> None:
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None and update.callback_query and update.callback_query.message:
        chat_id = update.callback_query.message.chat.id
    if chat_id is None and update.message:
        chat_id = update.message.chat.id
    if chat_id is None:
        return
    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )
