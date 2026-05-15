"""Basic command handlers (/start, /help, /ping, /news)."""
from __future__ import annotations

import logging
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from core_config import CFG
from telegram_bot.handlers.helpers import _current_ai_model, _send

log = logging.getLogger("tg.handlers")

# Зберігаємо очікувану відповідь капчі для кожного користувача
_captcha_answers: dict = {}


def get_keyboard() -> ReplyKeyboardMarkup:
    """Get main keyboard layout."""
    from telegram import ReplyKeyboardMarkup
    # MAXPILOT.md: Клавіатура команд — кожна команда окремою кнопкою, порядок як у документації
    return ReplyKeyboardMarkup(
        [
            ["/ai", "/analyze", "/panel"],
            ["/top", "/symbols", "/metals"],
            ["/metals_scalp", "/metals_kpi", "/kpi"],
            ["/orders", "/help", "/news"],
        ],
        resize_keyboard=True
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command with captcha for new users."""
    from utils.captcha import is_user_verified, generate_captcha, create_captcha_keyboard
    from utils.texts import GUIDE_SIGNAL_UA
    
    user_id = update.effective_user.id
    
    # Якщо вже верифікований - показуємо меню + гайд
    if is_user_verified(user_id):
        await _send(update, context, "👋 Привіт! Я трейд-бот. Команди нижче.", reply_markup=get_keyboard())
        # Автоматично показуємо гайд
        await update.message.reply_text(
            GUIDE_SIGNAL_UA,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        return
    
    # Генеруємо капчу для нового користувача
    question, correct_answer, options = generate_captcha()
    _captcha_answers[user_id] = correct_answer
    
    keyboard = create_captcha_keyboard(options)
    
    await update.message.reply_text(
        f"🤖 <b>Перевірка на бота</b>\n\n"
        f"{question}\n\n"
        f"<i>Це захист від спаму. Вибери правильний емодзі.</i>",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


async def handle_captcha_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle captcha button press."""
    from utils.captcha import set_user_verified
    
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data  # "captcha:😀"
    
    if not data.startswith("captcha:"):
        return
    
    selected_emoji = data.split(":", 1)[1]
    correct_emoji = _captcha_answers.get(user_id)
    
    if not correct_emoji:
        # Капча вже застаріла або не знайдена
        await query.edit_message_text("⚠️ Капча застаріла. Натисни /start знову.")
        return
    
    if selected_emoji == correct_emoji:
        # Правильно!
        set_user_verified(user_id, True)
        _captcha_answers.pop(user_id, None)
        
        from utils.texts import GUIDE_SIGNAL_UA
        
        await query.edit_message_text(
            "✅ <b>Верифікація пройдена!</b>\n\n"
            "Ласкаво просимо! Ось твоє меню команд 👇",
            parse_mode="HTML"
        )
        
        # Надсилаємо клавіатуру
        await query.message.reply_text(
            "👋 Привіт! Я трейд-бот. Команди нижче.",
            reply_markup=get_keyboard()
        )
        
        # Автоматично показуємо гайд
        await query.message.reply_text(
            GUIDE_SIGNAL_UA,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    else:
        # Неправильно - генеруємо нову капчу
        from utils.captcha import generate_captcha, create_captcha_keyboard
        
        question, correct_answer, options = generate_captcha()
        _captcha_answers[user_id] = correct_answer
        keyboard = create_captcha_keyboard(options)
        
        await query.edit_message_text(
            f"❌ <b>Неправильно!</b> Спробуй ще раз.\n\n"
            f"{question}\n\n"
            f"<i>Вибери правильний емодзі.</i>",
            reply_markup=keyboard,
            parse_mode="HTML"
        )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    text = (
        "🆘 *Довідка*\n\n"
        "*📋 Основні команди:*\n"
        "• `/top` — Топ-20 USDT пар (Volume/Gainers)\n"
        "• `/analyze` — Твої монети з панелі + Analyze ALL\n"
        "• `/symbols` — Повний список монет моніторингу\n"
        "• `/metals [TF]` — Окремий блок золото/срібло\n"
        "• `/metals_scalp [TF]` — Скальпер по цінних металах\n"
        "• `/metals_kpi [days]` — KPI metals-угод\n"
        "• `/ai <SYM>` — AI-план (Entry/SL/TP, RR)\n"
        "• `/req <SYM>` — Залежність від BTC/ETH\n"
        "• `/news` — Останні новини крипто\n\n"
        "*📊 Статистика:*\n"
        "• `/kpi` — KPI трейдів (winrate, PnL)\n"
        "• `/orders` — Відкриті ордери\n"
        "• `/ls [SYM]` — Long/Short Ratio з Binance\n\n"
        "*⚙️ Налаштування:*\n"
        "• `/panel` — Панель налаштувань\n"
        "• `/neutral` — Режим Neutral (CLOSE/TRAIL)\n\n"
        "*📈 Що показує /ls:*\n"
        "• % трейдерів в LONG/SHORT\n"
        "• L/S Ratio (>1.5 = багато лонгів)\n"
        "• Топ-трейдери окремо\n"
        "• Open Interest\n\n"
        "*⚡ Скальпінг:*\n"
        "В `/panel` увімкни Scalping Mode:\n"
        "• SL/TP в % від ціни\n"
        "• Slippage враховано\n"
        "• Gate-логіка (12 індикаторів)\n\n"
        f"🧠 Модель: `{_current_ai_model()}`\n"
        f"⏱ TZ: `{CFG['tz']}`"
    )
    await _send(update, context, text, parse_mode="Markdown")


async def metals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /metals command."""
    try:
        from market_data.metals import format_metals_report, parse_metals

        args = [a.strip() for a in (context.args or []) if a.strip()]
        tf_options = {"1m", "5m", "15m", "30m", "1h", "4h", "1d"}
        symbols = parse_metals(",".join(CFG.get("metals_symbols", []) or []))
        timeframe = "1h"

        if args:
            first = args[0].lower()
            if first in tf_options:
                timeframe = first
            else:
                symbols = parse_metals(args[0])
                if len(args) > 1 and args[1].lower() in tf_options:
                    timeframe = args[1].lower()

        await _send(update, context, "⏳ Рахую metals-блок…")
        report = format_metals_report(symbols, timeframe=timeframe, limit=180)
        await _send(update, context, report, parse_mode="Markdown")
    except Exception as e:
        log.exception("/metals failed")
        await _send(update, context, f"⚠️ metals error: {e}")


async def metals_scalp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /metals_scalp command."""
    try:
        from market_data.metals import format_metals_scalp_report, parse_metals

        args = [a.strip() for a in (context.args or []) if a.strip()]
        tf_options = {"1m", "5m", "15m", "30m", "1h", "4h", "1d"}
        timeframe = None
        symbols = parse_metals(",".join(CFG.get("metals_symbols", []) or []))
        if args:
            first = args[0].lower()
            if first in tf_options:
                timeframe = first
            else:
                symbols = parse_metals(args[0])
                if len(args) > 1 and args[1].lower() in tf_options:
                    timeframe = args[1].lower()

        await _send(update, context, "⏳ Рахую metals-scalp…")
        report = format_metals_scalp_report(symbols=symbols, timeframe=timeframe)
        await _send(update, context, report, parse_mode="Markdown")
    except Exception as e:
        log.exception("/metals_scalp failed")
        await _send(update, context, f"⚠️ metals scalp error: {e}")


async def metals_kpi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /metals_kpi command."""
    try:
        from services.metals_autopost import metals_kpi_summary

        days = 7
        if context.args:
            try:
                days = max(1, min(90, int(context.args[0])))
            except Exception:
                days = 7
        await _send(update, context, metals_kpi_summary(days=days), reply_markup=_metals_kpi_keyboard(days))
    except Exception as e:
        log.exception("/metals_kpi failed")
        await _send(update, context, f"⚠️ metals KPI error: {e}")


def _metals_kpi_keyboard(days: int = 7) -> InlineKeyboardMarkup:
    presets = [1, 3, 7, 14, 30]

    def label(d: int) -> str:
        return f"✅ {d}д" if int(days) == d else f"{d}д"

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label(d), callback_data=f"metals_kpi:{d}") for d in presets],
        [
            InlineKeyboardButton("⚡ Metals Scalp", callback_data="panel:metals_scalp:"),
            InlineKeyboardButton("📦 Orders", callback_data="orders:refresh"),
        ],
    ])


async def metals_kpi_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle metals KPI period buttons."""
    q = update.callback_query
    if not q or not q.data:
        return
    await q.answer()
    try:
        from services.metals_autopost import metals_kpi_summary

        days = max(1, min(90, int(q.data.split(":", 1)[1])))
        await q.edit_message_text(
            metals_kpi_summary(days=days),
            reply_markup=_metals_kpi_keyboard(days),
            disable_web_page_preview=True,
        )
    except Exception as e:
        log.exception("metals KPI callback failed")
        await q.edit_message_text(f"⚠️ metals KPI error: {e}")


async def guide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /guide command."""
    text = (
        "🧮 Як працюють індикатори у плані /ai:\n"
        "• Тренд: EMA/SMA (нахил, перетини), якщо ціна > EMA(50/200) — перевага LONG.\n"
        "• Моментум: RSI, MACD — імпульс/розвороти (RSI<30 — перепроданість, RSI>70 — перекупленість).\n"
        "• Волатильність: ATR, Bollinger — ширина ходу, адекватність SL/TP.\n"
        "• Сила тренду: ADX, CCI — ADX>20-25 досить для слідування.\n"
        "• Обʼєм: OBV/MFI — підтвердження руху.\n"
        "• Pivots: рівні для Entry/SL/TP.\n\n"
        "📐 RR: LONG=(TP−Entry)/(Entry−SL), SHORT навпаки. Фільтр: RR<1.5 — скіп.\n"
    )
    await _send(update, context, text, parse_mode="Markdown")


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /ping command."""
    await _send(update, context, f"🏓 pong all ok | AI model: {_current_ai_model()}")


async def news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /news command - швидкі крипто-новини."""
    try:
        from utils.news_fetcher import get_latest_news_async
        args = context.args or []
        query = " ".join(args).strip() if args else None
        pref = (CFG.get("default_locale") or "uk").lower()
        lang = "uk" if pref in ("uk", "ua") else "en"
        
        # Показуємо, що завантажуємо
        loading_msg = await update.message.reply_text("🔄 Завантажую новини...")
        
        items = await get_latest_news_async(query=query, max_items=8, lang=lang)
        
        if not items:
            await loading_msg.edit_text("📰 Немає свіжих заголовків зараз.")
            return
        
        # Гарне форматування
        if query:
            header = f"📰 <b>Новини: {query}</b>"
        else:
            header = "📰 <b>Останні крипто-новини</b>"
        
        lines = [header, "━━━━━━━━━━━━━━━━━━━━━━━━━"]
        for i, it in enumerate(items, 1):
            title = it.get("title") or ""
            link = it.get("link") or ""
            src = it.get("source") or ""
            time_ago = it.get("time_ago") or ""
            
            # Емодзі для номера
            num_emoji = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"][i-1] if i <= 8 else f"{i}."
            
            # Форматуємо рядок - title як клікабельне посилання
            time_str = f" • {time_ago}" if time_ago else ""
            lines.append(f"{num_emoji} <a href=\"{link}\">{title}</a>\n      <i>{src}{time_str}</i>")
        
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("💡 <code>/news bitcoin</code> — пошук")
        
        await loading_msg.edit_text(
            "\n".join(lines)[:4000], 
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    except Exception as e:
        log.exception("/news failed")
        await _send(update, context, f"⚠️ news error: {e}")
