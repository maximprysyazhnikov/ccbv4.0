"""Handler registration for Telegram bot."""
from __future__ import annotations

from telegram.ext import Application, CommandHandler, CallbackQueryHandler

from telegram_bot.handlers.commands import start, help_cmd, guide, ping, news, metals, metals_scalp, metals_kpi, metals_kpi_cb, handle_captcha_callback
from telegram_bot.handlers.panel_handlers import panel, symbols, on_cb_panel
from telegram_bot.handlers.top_handlers import top, on_cb_sym, on_cb_topmode
from telegram_bot.handlers.ai_commands import (
    req, analyze, on_cb_analyze_all, on_cb_an_refresh, on_cb_goto_panel,
    cmd_ai, on_cb_ai, on_cb_indicators, on_cb_dep
)
from telegram_bot.handlers.kpi_handlers import daily_now, winrate_now
from telegram_bot.handlers.callbacks import autopost_now


def register_handlers(app: Application):
    """Register all command and callback handlers."""
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("guide", guide))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("panel", panel))
    app.add_handler(CommandHandler("symbols", symbols))
    app.add_handler(CommandHandler("metals", metals))
    app.add_handler(CommandHandler("metals_scalp", metals_scalp))
    app.add_handler(CommandHandler("metals_kpi", metals_kpi))
    app.add_handler(CommandHandler("top", top))
    app.add_handler(CommandHandler("analyze", analyze))
    
    # Important: /ai must be first and block others
    app.add_handler(CommandHandler("ai", cmd_ai, block=True), group=-100)
    
    app.add_handler(CommandHandler("req", req))
    app.add_handler(CommandHandler("news", news))
    app.add_handler(CommandHandler("daily_now", daily_now))
    app.add_handler(CommandHandler("winrate_now", winrate_now))
    app.add_handler(CommandHandler("autopost_now", autopost_now))
    
    # Captcha callback handler (high priority)
    app.add_handler(CallbackQueryHandler(handle_captcha_callback, pattern=r"^captcha:.+$"), group=-50)
    app.add_handler(CallbackQueryHandler(metals_kpi_cb, pattern=r"^metals_kpi:\d+$"))
    
    # Pattern excludes edit_symbols which is handled by ConversationHandler in main.py
    app.add_handler(CallbackQueryHandler(on_cb_panel, pattern=r"^panel:(?!edit_symbols).+"))
    app.add_handler(CallbackQueryHandler(on_cb_sym, pattern=r"^sym:[A-Z0-9]+$"))
    app.add_handler(CallbackQueryHandler(on_cb_ai, pattern=r"^ai:[A-Z0-9]+$"))
    app.add_handler(CallbackQueryHandler(on_cb_indicators, pattern=r"^indic:[A-Z0-9]+$"))
    app.add_handler(CallbackQueryHandler(on_cb_dep, pattern=r"^dep:[A-Z0-9]+$"))
    app.add_handler(CallbackQueryHandler(on_cb_topmode, pattern=r"^topmode:(volume|gainers)$"))
    app.add_handler(CallbackQueryHandler(on_cb_analyze_all, pattern=r"^an_all$"))
    app.add_handler(CallbackQueryHandler(on_cb_an_refresh, pattern=r"^an_refresh$"))
    app.add_handler(CallbackQueryHandler(on_cb_goto_panel, pattern=r"^goto_panel$"))
