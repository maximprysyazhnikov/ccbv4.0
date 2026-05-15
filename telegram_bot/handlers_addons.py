# telegram_bot/handlers_addons.py
from __future__ import annotations
from telegram.ext import Application, CallbackQueryHandler, CommandHandler
from telegram_bot import panel_neutral  # /neutral, /kpi і їх callback-и
from services.daily_tracker import compute_daily_summary

async def cmd_daily_now(update, context):
    try:
        text = compute_daily_summary()  # без аргументів!
        await update.effective_chat.send_message(text)
    except Exception as e:
        await update.effective_chat.send_message(f"⚠️ daily_now error: {e}")


async def cmd_sentiment(update, context):
    """
    /sentiment [SYMBOL] — показує Long/Short Ratio з Binance.
    Приклади: /sentiment, /sentiment ETHUSDT
    """
    from market_data.long_short_ratio import get_full_sentiment, format_sentiment_text
    
    # Визначаємо символ з аргументів або беремо BTCUSDT
    symbol = "BTCUSDT"
    if context.args:
        symbol = context.args[0].upper()
        if not symbol.endswith("USDT"):
            symbol += "USDT"
    
    await update.effective_chat.send_message(f"⏳ Отримую дані для {symbol}...")
    
    try:
        data = await get_full_sentiment(symbol, period="5m")
        if data:
            text = format_sentiment_text(data)
        else:
            text = f"⚠️ Не вдалося отримати дані для {symbol}"
        await update.effective_chat.send_message(text, parse_mode="Markdown")
    except Exception as e:
        await update.effective_chat.send_message(f"⚠️ Помилка: {e}")


async def on_cb_ls(update, context):
    """Callback handler for ls:{SYMBOL} button — shows Long/Short Ratio."""
    from market_data.long_short_ratio import get_full_sentiment, format_sentiment_text
    
    q = update.callback_query
    await q.answer()
    data = (q.data or "")
    if not data.startswith("ls:"):
        return
    
    symbol = data.split(":", 1)[1].upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"
    
    await update.effective_chat.send_message(f"⏳ L/S Ratio для {symbol}...")
    
    try:
        sent_data = await get_full_sentiment(symbol, period="5m")
        if sent_data:
            text = format_sentiment_text(sent_data)
        else:
            text = f"⚠️ Не вдалося отримати дані для {symbol}"
        await update.effective_chat.send_message(text, parse_mode="Markdown")
    except Exception as e:
        await update.effective_chat.send_message(f"⚠️ Помилка: {e}")

def register_extra(app: Application):
    # Команда /sentiment для Long/Short Ratio
    app.add_handler(CommandHandler("sentiment", cmd_sentiment))
    app.add_handler(CommandHandler("ls", cmd_sentiment))  # аліас
    
    # Callback для кнопки ls:{SYMBOL}
    app.add_handler(CallbackQueryHandler(on_cb_ls, pattern=r"^ls:"))
    
    # Кнопки з /panel → ті самі екрани, що й /neutral та /kpi
    app.add_handler(CallbackQueryHandler(panel_neutral.cmd_neutral, pattern=r"^panel:neutral$"))
    app.add_handler(CallbackQueryHandler(panel_neutral.cmd_kpi,     pattern=r"^panel:kpi$"))
    # Кнопки вибору режиму на екрані Neutral (CLOSE/TRAIL/IGNORE)
    app.add_handler(CallbackQueryHandler(panel_neutral.cb_neutral,  pattern=r"^neutral_mode:"))
