"""Generic callback handlers."""
from __future__ import annotations

import logging
import inspect
from telegram import Update
from telegram.ext import ContextTypes

from services.autopost import run_autopost_once
from telegram_bot.handlers.helpers import _send

log = logging.getLogger("tg.handlers")


async def autopost_now(update, context):
    """Handle /autopost_now command."""
    chat_id = update.effective_chat.id
    try:
        await context.bot.send_message(chat_id, "⏳ Запускаю автопост…")
        
        if inspect.iscoroutinefunction(run_autopost_once):
            msgs = await run_autopost_once(context.application)
        else:
            import asyncio
            msgs = await asyncio.to_thread(run_autopost_once, context.application)
        
        sent = 0
        for m in msgs or []:
            try:
                await context.bot.send_message(
                    m.get("chat_id", chat_id),
                    m.get("text", ""),
                    parse_mode=m.get("parse_mode"),
                    disable_web_page_preview=m.get("disable_web_page_preview", True),
                )
                sent += 1
            except Exception as e:
                logging.getLogger("autopost").warning("send fail: %s", e)
        await context.bot.send_message(chat_id, f"✅ Готово: автопост надіслав {sent} повідомлень.")
    except Exception as e:
        await context.bot.send_message(chat_id, f"⚠️ autopost_now error: {e}")
