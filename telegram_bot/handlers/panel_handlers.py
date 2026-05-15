"""Panel handlers (/panel and callbacks)."""
from __future__ import annotations

import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from trader.risk_manager import RiskManager
from telegram_bot.panel import panel_keyboard, apply_panel_action, symbols_overview_text
from utils.user_settings import ensure_user_row

log = logging.getLogger("tg.handlers")


async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Open settings panel for current user."""
    try:
        uid = update.effective_user.id
        ensure_user_row(uid)
        kb = panel_keyboard(uid)
        await _send(update, context, "Панель налаштувань:", reply_markup=kb)
    except Exception as e:
        log.exception("/panel failed")
        await _send(update, context, f"⚠️ panel error: {e}")


async def symbols(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show full monitored symbols list for current user."""
    try:
        uid = update.effective_user.id
        ensure_user_row(uid)
        await _send(
            update,
            context,
            symbols_overview_text(uid),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⚙️ Panel", callback_data="goto_panel"),
                InlineKeyboardButton("✏️ Edit", callback_data="panel:edit_symbols:"),
            ]]),
        )
    except Exception as e:
        log.exception("/symbols failed")
        await _send(update, context, f"⚠️ symbols error: {e}")


async def on_cb_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle panel button clicks."""
    q = update.callback_query
    try:
        data = (q.data or "")
        if not data.startswith("panel:"):
            return
        
        # Skip edit_symbols - handled by ConversationHandler
        if data.startswith("panel:edit_symbols"):
            return
        
        _p, action, value = (data.split(":", 2) + ["", ""])[0:3]
        uid = q.from_user.id
        log.info(f"[panel] user={uid} action={action} value={value}")
        
        ensure_user_row(uid)
        apply_panel_action(uid, action, value)

        if action == "show_symbols":
            await q.answer()
            await _send(
                update,
                context,
                symbols_overview_text(uid),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⚙️ Panel", callback_data="goto_panel"),
                    InlineKeyboardButton("✏️ Edit", callback_data="panel:edit_symbols:"),
                ]]),
            )
            return

        if action == "show_metals":
            await q.answer()
            from market_data.metals import format_metals_report, parse_metals
            from core_config import CFG

            await _send(update, context, "⏳ Рахую metals-блок…")
            report = format_metals_report(
                parse_metals(",".join(CFG.get("metals_symbols", []) or [])),
                timeframe="1h",
                limit=180,
            )
            await _send(
                update,
                context,
                report,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⚡ Metals Scalp", callback_data="panel:metals_scalp:"),
                    InlineKeyboardButton("⚙️ Panel", callback_data="goto_panel"),
                ]]),
            )
            return

        if action == "metals_scalp":
            await q.answer()
            from market_data.metals import format_metals_scalp_report

            await _send(update, context, "⏳ Рахую metals-scalp…")
            report = format_metals_scalp_report()
            await _send(
                update,
                context,
                report,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🥇 Metals", callback_data="panel:show_metals:"),
                    InlineKeyboardButton("⚙️ Panel", callback_data="goto_panel"),
                ]]),
            )
            return

        if action == "metals_kpi":
            await q.answer()
            from services.metals_autopost import metals_kpi_summary
            from telegram_bot.handlers.commands import _metals_kpi_keyboard

            await _send(
                update,
                context,
                metals_kpi_summary(days=7),
                reply_markup=_metals_kpi_keyboard(7),
            )
            return
        
        if action == "help":
            await q.answer()
            await _send(
                update, context,
                "ℹ️ *Панель*\n"
                "- Autopost — вкл/викл фоновий аналіз моніторинг-пар.\n"
                "- Scalping — режим скальпінгу з фіксованим SL/TP%.\n"
                "- TF — твій дефолтний таймфрейм (для /ai, /req тощо).\n"
                "- AP TF — таймфрейм автопосту.\n"
                "- AP RR — мінімальний Risk/Reward для автопосту.\n"
                "- Model — 'auto' або конкретна модель зі слотів.\n"
                "- Locale — мова відповідей (UK/EN).\n"
                "- Daily/Winrate — щоденний P&L і тижневий winrate.\n"
                "- Symbols — власний список монет для моніторингу.\n",
                parse_mode="Markdown"
            )
            return

        if action == "risk_monitor":
            await q.answer()
            await _show_risk_monitor(update, context, uid)
            return
        
        # Update keyboard with new state
        new_kb = panel_keyboard(uid)
        try:
            await q.answer(f"✓ {action}")
            await q.edit_message_reply_markup(reply_markup=new_kb)
            log.info(f"[panel] keyboard updated for user={uid}")
        except Exception as e:
            log.warning(f"[panel] edit_message_reply_markup failed: {e}")
            # Fallback: send new message
            await _send(update, context, "Панель налаштувань:", reply_markup=new_kb)
    
    except Exception as e:
        log.exception("on_cb_panel failed")
        try:
            await q.answer(f"⚠️ Error: {e}")
        except (Exception) as e:
            log.warning(f"Unexpected error: {e}")
            pass
        await _send(update, context, f"⚠️ panel cb error: {e}")


async def _show_risk_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Show risk monitoring dashboard"""
    try:
        risk_mgr = RiskManager()
        metrics = risk_mgr.get_risk_metrics()

        if "error" in metrics:
            await _send(update, context, f"⚠️ Risk monitor error: {metrics['error']}")
            return

        # Circuit breaker status
        cb_status = "🚨 ACTIVE" if metrics["circuit_breaker_active"] else "✅ NORMAL"
        cb_reason = f"\nReason: {metrics['circuit_breaker_reason']}" if metrics["circuit_breaker_active"] else ""

        # Risk metrics
        message = (
            f"🚨 <b>RISK MONITORING DASHBOARD</b>\n\n"
            f"🔴 <b>Circuit Breaker:</b> {cb_status}{cb_reason}\n\n"
            f"📊 <b>Current Metrics:</b>\n"
            f"• Consecutive Losses: <b>{metrics['consecutive_losses']}</b> (max: {metrics['limits']['max_consecutive_losses']})\n"
            f"• Daily P&L: <b>{metrics['daily_rr']:+.2f}R</b> (limit: -{metrics['limits']['max_daily_drawdown_r']:.2f}R)\n"
            f"• Weekly P&L: <b>{metrics['weekly_rr']:+.2f}R</b> (limit: -{metrics['limits']['max_weekly_drawdown_r']:.2f}R)\n"
            f"• Win Rate (20): <b>{metrics['win_rate_20']:.1%}</b> (min: {metrics['limits']['min_win_rate']:.1%})\n"
            f"• Win Rate (50): <b>{metrics['win_rate_50']:.1%}</b>\n\n"
            f"⚙️ <b>Risk Limits:</b>\n"
            f"• Max Daily DD: -{metrics['limits']['max_daily_drawdown_r']:.2f}R\n"
            f"• Max Weekly DD: -{metrics['limits']['max_weekly_drawdown_r']:.2f}R\n"
            f"• Max Consec Losses: {metrics['limits']['max_consecutive_losses']}\n"
            f"• Min Win Rate: {metrics['limits']['min_win_rate']:.1%}\n"
            f"• Circuit Breaker Cooldown: {risk_mgr.limits.circuit_breaker_cooldown_hours}h"
        )

        await _send(update, context, message, parse_mode="HTML")

    except Exception as e:
        log.exception("Risk monitor failed")
        await _send(update, context, f"⚠️ Risk monitor error: {e}")


async def _send(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str,
                *, parse_mode=None, reply_markup=None) -> None:
    """Send message to chat, handling both regular messages and callback queries."""
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
