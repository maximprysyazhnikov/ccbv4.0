from __future__ import annotations
from services.signal_closer import auto_close_tp_sl

# ───────────────────────────────────────────────
# Auto-close TP/SL job
async def auto_close_tp_sl_job(context) -> None:
    """Періодичне автозакриття по TP/SL для всіх відкритих трейдів."""

    try:
        closed = await _run_maybe_async(auto_close_tp_sl)
        if closed:
            log.info(f"auto_close_tp_sl_job: closed {closed} trades by TP/SL.")
    except Exception as e:
        log.warning(f"auto_close_tp_sl_job failed: {e}")


from dotenv import load_dotenv
load_dotenv()

import os, logging, sys
print("[diag] sys.path =", sys.path)
print("[diag] CWD =", os.getcwd())
print("[diag] ENV.DB_PATH =", os.environ.get("DB_PATH"))
try:
    os.makedirs("/data", exist_ok=True)
    open("/data/_rw_test", "w").write("ok")
    print("[diag] /data write OK")
except Exception as e:
    print("[diag] /data write FAIL:", e)

import os, logging
logging.getLogger("db").setLevel(logging.INFO)

try:
    os.makedirs("/data", exist_ok=True)
    open("/data/_rw_test", "w").write("ok")
    print("[fs] /data write OK")
except Exception as e:
    print("[fs] /data write FAIL:", e)

import asyncio
import logging
import os
import inspect

from dotenv import load_dotenv
load_dotenv("env")

# вмикає TTL-кеш для get_setting
import utils.settings_cached  # noqa: F401

import time
from datetime import time as dtime, timezone
from zoneinfo import ZoneInfo
from utils.db_migrate import migrate_if_needed
migrate_if_needed()
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants
from telegram.ext import (
    Application,
    AIORateLimiter,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters as tg_filters,
)

import sitecustomize  # noqa: F401  # LLM-guard

from core_config import CFG
from services.autopost import mark_autopost_sent, run_autopost_once
from services.daily_tracker import daily_tracker_job
from services.decision_log import log_decision
from services.kpi import kpi_summary
from services.risk_report import (
    build_daily_risk_report,
    build_decision_report,
    build_edge_report,
    build_open_risk_report,
    build_settings_audit,
)
from services.paper_signals import (
    build_allowlist_proposal,
    build_paper_report,
    build_release_report,
    close_paper_signals_once,
    get_allowlist_proposal_state,
)
from services.winrate_tracker import winrate_job
from services.autopost_bridge import handle_autopost_message
from services.metals_autopost import close_metals_trades_once, run_metals_autopost_once

from telegram_bot.handlers import register_handlers  # ваш catch-all "^panel:"
from telegram_bot.handlers_addons import register_extra  # /daily_now, /winrate_now, panel:neutral/kpi
from telegram_bot import panel_neutral  # ⚙️ Neutral і 📊 KPI
from telegram_bot.handlers_help import cmd_help, cmd_guide, show_signal_guide

# ───────────────────────────────────────────────
# optional integrations (best-effort imports)
# ───────────────────────────────────────────────
_close_fn = None
try:
    from services.signal_closer import check_and_close_neutral as _close_fn
except Exception:
    try:
        from services.signal_closer import close_signals_once as _close_fn
    except Exception:
        _close_fn = None

try:
    from services.position_manager import manage_open_positions as _pm_fn
except Exception:
    _pm_fn = None

try:
    from services.signal_sync import sync_signals_once
except Exception:
    sync_signals_once = None

try:
    from alerts.push_alerts import run_alerts_once as _alerts_fn
except Exception:
    _alerts_fn = None

# ───────────────────────────────────────────────
# globals & logging
# ───────────────────────────────────────────────
TZ = ZoneInfo(os.getenv("TZ_NAME", "Europe/Kyiv"))

# Setup structured logging
from utils.logging_config import setup_logging
log_file = os.getenv("LOG_FILE", "logs/app.log")
use_json = os.getenv("LOG_JSON", "false").lower() in ("true", "1", "yes")
setup_logging(log_level=os.getenv("LOG_LEVEL"), log_file=log_file if log_file else None, use_json=use_json)

log = logging.getLogger("app")
METALS_TRADE_MODE = "metals_scalping"
logging.getLogger("llm_guard").setLevel(logging.ERROR)


# ───────────────────────────────────────────────
# helpers: universal maybe-async runner
# ───────────────────────────────────────────────
async def _run_maybe_async(fn, /, *args, **kwargs):
    """
    Виконує fn як async (await), або як sync у thread; якщо sync-функція повернула корутину — теж await.
    """
    if fn is None:
        return None
    try:
        if inspect.iscoroutinefunction(fn):
            return await fn(*args, **kwargs)
        res = await asyncio.to_thread(fn, *args, **kwargs)
        if asyncio.iscoroutine(res):
            return await res
        return res
    except Exception:
        log.warning("runner failed for %s", getattr(fn, "__name__", fn), exc_info=True)
        return None


# ───────────────────────────────────────────────
# /kpi helpers ✨
# ───────────────────────────────────────────────
def _parse_kpi_args(args: list[str]) -> tuple[str, int]:
    """Підтримує /kpi, /kpi 3, /kpi trades 7, /kpi signals 14, /kpi 14 trades."""
    table = "trades"
    days = 7
    if not args:
        return table, days

    if len(args) == 1:
        if args[0].isdigit():
            days = int(args[0])
        else:
            a = args[0].lower()
            table = "signals" if a.startswith("sig") else "trades"
        return table, max(1, days)

    a, b = args[0], args[1]
    a_is_num, b_is_num = a.isdigit(), b.isdigit()
    if a_is_num and not b_is_num:
        days = int(a)
        table = "signals" if b.lower().startswith("sig") else "trades"
    elif b_is_num and not a_is_num:
        table = "signals" if a.lower().startswith("sig") else "trades"
        days = int(b)
    else:
        if a_is_num:
            days = int(a)
        if b.lower().startswith("sig"):
            table = "signals"

    return table, max(1, days)


def _kpi_keyboard(table: str, days: int) -> InlineKeyboardMarkup:
    """Інлайн-клавіатура: пресети днів і перемикач таблиці."""
    other_table = "signals" if table == "trades" else "trades"
    presets = [1, 3, 7, 14, 30]

    def _label(d):
        return f"✅ {d}д" if d == days else f"{d}д"

    rows = [
        [InlineKeyboardButton(_label(d), callback_data=f"kpi:{table}:{d}") for d in presets],
        [InlineKeyboardButton(f"Switch → {other_table}", callback_data=f"kpi:{other_table}:{days}" )],
        [InlineKeyboardButton("� Show breakdown", callback_data=f"kpi:break:{table}:{days}")],
        [InlineKeyboardButton("�📦 Open Orders", callback_data="orders:refresh")],
        [InlineKeyboardButton("❌ Done", callback_data="kpi:close")],
    ]
    return InlineKeyboardMarkup(rows)


def _append_pnl_bars(report_text: str) -> str:
    """Парсить табличку KPI та додає компактний ASCII-bar по PnL наприкінці."""
    lines = report_text.splitlines()
    rows: list[tuple[str, float]] = []
    in_rows = False

    for ln in lines:
        if not in_rows and "Symbol" in ln and "PnL" in ln:
            in_rows = True
            continue
        if in_rows:
            if ln.strip().startswith("TOTAL"):
                break
            if set(ln.strip()) <= {"─", "—", " ", "─"} or not ln.strip():
                continue
            parts = ln.split()
            if len(parts) < 2:
                continue
            symbol = parts[0]
            pnl = None
            for tok in reversed(parts):
                tok2 = tok.replace(",", "")
                try:
                    pnl = float(tok2)
                    break
                except ValueError:
                    continue
            if pnl is None:
                continue
            rows.append((symbol, pnl))

    nonzero = [(s, p) for s, p in rows if abs(p) > 1e-12]
    if not nonzero:
        return report_text + "\n\nPnL bars: всі значення 0.00"

    max_abs = max(abs(p) for _, p in nonzero) or 1.0
    min_abs = min(abs(p) for _, p in nonzero) or 0.0
    min_width = 1
    max_width = 12

    def mk_bar(v: float) -> str:
        # Пропорційна ширина, але мінімум 1, максимум 12
        width = min_width
        if max_abs > 0:
            width = int(round((abs(v) / max_abs) * max_width))
        width = max(min_width, min(width, max_width))
        bar = "#" * width
        return f"+{bar}" if v > 0 else f"-{bar}"

    nonzero.sort(key=lambda x: abs(x[1]), reverse=True)
    lines_bars = []
    for s, p in nonzero[:8]:
        sign = "+" if p >= 0 else "-"
        lines_bars.append(f"{s:<8} | {mk_bar(p):<13} | {sign}{abs(p):.2f}")

    return report_text + "\n\nPnL bars:\n" + "\n".join(lines_bars)


def _same_markup(a, b) -> bool:
    """Безпечно порівнює InlineKeyboardMarkup (або None) через dict-подання."""
    try:
        if a is None and b is None:
            return True
        if (a is None) != (b is None):
            return False
        return a.to_dict() == b.to_dict()
    except Exception:
        return False


# ───────────────────────────────────────────────
# /kpi command & callback ✨
# ───────────────────────────────────────────────
async def kpi_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /kpi [table] [days] — показує KPI та додає ASCII-bars по PnL."""
    table, days = _parse_kpi_args(context.args or [])
    try:
        text = kpi_summary(days=days, table=table)
    except Exception as e:
        text = f"❌ KPI error: {e}"
    text = _append_pnl_bars(text)
    await update.effective_chat.send_message(text, reply_markup=_kpi_keyboard(table, days))


async def kpi_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Інлайн-кнопки KPI: kpi:<table>:<days> або kpi:close."""
    q = update.callback_query
    await q.answer()

    data = (q.data or "")
    if data == "kpi:close":
        # Close the KPI inline view
        try:
            await q.edit_message_text(text="KPI view closed.")
        except Exception:
            pass
        return

    try:
        _, table, days_str = data.split(":")
        days = int(days_str)
    except Exception:
        table, days = "trades", 7

    try:
        text = kpi_summary(days=days, table=table)
    except Exception as e:
        text = f"❌ KPI error: {e}"

    text = _append_pnl_bars(text)
    new_markup = _kpi_keyboard(table, days)

    old_text = q.message.text if q.message else None
    old_markup = q.message.reply_markup if q.message else None
    if old_text == text and _same_markup(old_markup, new_markup):
        await q.answer("Вже оновлено ✅", show_alert=False)
        return

    await q.edit_message_text(text=text, reply_markup=new_markup)


async def kpi_break_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show detailed breakdown per-symbol and per-mode for KPI.

    Callback forms:
      - kpi:break:trades:7            -> show all symbols breakdown
      - kpi:break:trades:7:BTCUSDT    -> show breakdown for BTCUSDT only
    """
    q = update.callback_query
    await q.answer()
    data = (q.data or "")

    parts = data.split(":")
    # parts = ['kpi','break','trades','7'] or ['kpi','break','trades','7','BTCUSDT']
    if len(parts) < 4:
        await q.message.reply_text("Invalid breakdown request")
        return

    _, _, table, days = parts[:4]
    symbol = parts[4] if len(parts) >= 5 else None

    # Query DB for breakdown
    from utils.db import get_conn
    since = int(time.time()) - int(days) * 86400

    with get_conn() as conn:
        cur = conn.cursor()
        # Check if trade_mode column exists and detect pnl column
        cols = [c[1] for c in cur.execute("PRAGMA table_info(%s)" % table).fetchall()]
        has_mode = "trade_mode" in cols
        pnl_col = "pnl_usd" if "pnl_usd" in cols else ("pnl" if "pnl" in cols else None)
        pnl_expr = pnl_col if pnl_col else "0"
        
        if has_mode:
            q_sql = f"""
            SELECT symbol, LOWER(COALESCE(trade_mode,'standard')) AS mode,
                   COUNT(*) AS n, ROUND(SUM(COALESCE({pnl_expr},0)),2) AS pnl_usd
            FROM {table}
            WHERE CAST(closed_at AS INTEGER) >= ?
            {"AND symbol=?" if symbol else ""}
            GROUP BY symbol, mode
            ORDER BY symbol, mode
            """
        else:
            q_sql = f"""
            SELECT symbol, 'standard' AS mode,
                   COUNT(*) AS n, ROUND(SUM(COALESCE({pnl_expr},0)),2) AS pnl_usd
            FROM {table}
            WHERE CAST(closed_at AS INTEGER) >= ?
            {"AND symbol=?" if symbol else ""}
            GROUP BY symbol
            ORDER BY symbol
            """
        params = (since,)
        if symbol:
            params = (since, symbol)
        rows = cur.execute(q_sql, params).fetchall()

    if not rows:
        await q.message.reply_text("No breakdown data for the requested period.")
        return

    # Build structured summary
    by_sym = {}
    max_abs_pnl = 0
    for sym, mode, n, pnl in rows:
        if sym not in by_sym:
            by_sym[sym] = {}
        by_sym[sym][mode] = {'n': int(n), 'pnl': float(pnl)}
        max_abs_pnl = max(max_abs_pnl, abs(float(pnl)))

    # PnL alert threshold
    PNL_ALERT = 50.0
    alert_lines = []
    for sym, modes in by_sym.items():
        total = sum(m['pnl'] for m in modes.values())
        if abs(total) >= PNL_ALERT:
            alert_lines.append(f"⚠️ {sym} PnL = {total:+.2f}$ exceeds threshold {PNL_ALERT}")

    lines = [f"📊 KPI breakdown ({table}) last {days}d", ""]
    # Add alert if any
    if alert_lines:
        lines.extend(alert_lines)
        lines.append("")

    # Per-symbol rows with drilldown buttons
    buttons = []
    for sym, modes in by_sym.items():
        parts = []
        for m in ('scalping', 'standard', 'ai'):
            if m in modes:
                parts.append(f"{m[:4]} {modes[m]['pnl']:+.2f}$ ({modes[m]['n']})")
        # Add a button for drilldown
        lines.append(f"{sym}: " + " | ".join(parts))
        buttons.append([InlineKeyboardButton(f"🔎 {sym}", callback_data=f"kpi:break:{table}:{days}:{sym}")])

    text = "\n".join(lines)

    # If this is a per-symbol drilldown, show recent trades for that symbol
    if symbol:
        with get_conn() as conn:
            cur = conn.cursor()
            # Try with trade_mode column first; if DB lacks it, fallback to no-mode query
            try:
                q_sql = f"""
                SELECT id, direction, entry, sl, tp, pnl_usd, closed_at, trade_mode FROM {table}
                WHERE symbol=? AND CAST(closed_at AS INTEGER) >= ?
                ORDER BY closed_at DESC LIMIT 10
                """
                rows = cur.execute(q_sql, (symbol, since)).fetchall()
            except Exception as e:
                # Likely 'no such column: trade_mode' — fallback
                cur = conn.cursor()
                q_sql = f"""
                SELECT id, direction, entry, sl, tp, pnl_usd, closed_at, NULL as trade_mode FROM {table}
                WHERE symbol=? AND CAST(closed_at AS INTEGER) >= ?
                ORDER BY closed_at DESC LIMIT 10
                """
                rows = cur.execute(q_sql, (symbol, since)).fetchall()
        if not rows:
            text += f"\n\nNo recent trades for {symbol}."
        else:
            text += f"\n\nRecent {symbol} trades (last {days}d):\n"
            text += "ID Dir Entry   SL     TP     PnL    Mode  Closed\n"
            for r in rows:
                # rows may have trade_mode NULL; handle safely
                id, direction, entry, sl, tp, pnl_usd, closed_at, trade_mode = r
                if trade_mode is None:
                    trade_mode = 'standard'
                closed_fmt = time.strftime('%m-%d %H:%M', time.localtime(int(closed_at))) if closed_at else "-"
                text += f"{id:3} {direction:>4} {entry:7.3f} {sl:7.3f} {tp:7.3f} {pnl_usd:7.2f} {str(trade_mode)[:3]:>3} {closed_fmt}\n"
        # Add a back button
        buttons = [[InlineKeyboardButton("⬅️ Back", callback_data=f"kpi:break:{table}:{days}")]]

    try:
        await q.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons) if buttons else None, parse_mode="Markdown")
    except Exception:
        try:
            await q.edit_message_text(text)
        except Exception:
            pass


# ───────────────────────────────────────────────
# /orders command & callback ✨
# ───────────────────────────────────────────────
def _get_open_orders(user_id: int = None) -> str:
    """Отримує список відкритих ордерів з бази, згрупованих по trade_mode."""
    from utils.db import get_conn
    from market_data.binance_data import get_latest_price
    from utils.user_settings import get_user_settings
    
    # Get user's monitored symbols
    us = get_user_settings(user_id) if user_id else {}
    monitored = us.get("monitored_symbols") or ""
    monitored_list = [s.strip().upper() for s in monitored.split(",") if s.strip()] if monitored else []
    
    with get_conn() as conn:
        # Check if trade_mode column exists
        cols = [c[1] for c in conn.execute("PRAGMA table_info(trades)").fetchall()]
        has_mode = "trade_mode" in cols
        
        if has_mode:
            rows = conn.execute(
                "SELECT id, symbol, timeframe, direction, entry, sl, tp, opened_at, trade_mode FROM trades WHERE status='OPEN' ORDER BY trade_mode, id"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, symbol, timeframe, direction, entry, sl, tp, opened_at FROM trades WHERE status='OPEN' ORDER BY id"
            ).fetchall()

    # Exclude known test symbols (e.g., TEST*, TPAIR) from /orders display
    import re
    def _is_test_symbol(sym: str) -> bool:
        try:
            return bool(re.match(r'^(TEST|TPAIR)', (sym or '').upper()))
        except Exception:
            return False

    # Filter out rows where symbol looks like a test placeholder
    if rows:
        filtered = []
        for r in rows:
            sym = r[1] if len(r) > 1 else None
            if not _is_test_symbol(sym):
                filtered.append(r)
        rows = filtered

    if not rows:
        return "📭 Немає відкритих ордерів"
    
    # Group by trade_mode and prepare separate lists for presentation (scalping / ai / standard)
    standard_orders = []
    scalping_orders = []
    metals_orders = []
    ai_orders = []
    
    from datetime import datetime
    now = datetime.now(timezone.utc)

    def _get_order_price(symbol: str, timeframe: str, trade_mode: str) -> float:
        if trade_mode == "metals_scalping":
            from services.metals_autopost import _latest_price as _latest_metals_price
            price = _latest_metals_price(symbol, timeframe or "5m")
            if price is None:
                raise RuntimeError(f"no metals price for {symbol}")
            return float(price)
        return float(get_latest_price(symbol))
    
    for r in rows:
        if has_mode:
            tid, symbol, timeframe, direction, entry, sl, tp, opened_at, trade_mode = r
        else:
            tid, symbol, timeframe, direction, entry, sl, tp, opened_at = r
            trade_mode = "standard"
        
        # Mark if in monitored list
        in_monitor = "📍" if symbol in monitored_list else ""
        
        # Calculate time since open
        time_info = ""
        if opened_at:
            try:
                opened_dt = datetime.fromisoformat(opened_at.replace("Z", "+00:00")) if isinstance(opened_at, str) else opened_at
                if getattr(opened_dt, "tzinfo", None) is None:
                    opened_dt = opened_dt.replace(tzinfo=timezone.utc)
                else:
                    opened_dt = opened_dt.astimezone(timezone.utc)
                delta = now - opened_dt
                mins = int(delta.total_seconds() / 60)
                if mins < 60:
                    time_info = f" ⏱️`{mins}m`"
                else:
                    hrs = mins // 60
                    time_info = f" ⏱️`{hrs}h{mins % 60}m`"
            except Exception:
                pass
        
        # Get current price
        try:
            price = _get_order_price(symbol, timeframe, trade_mode)
            
            # PnL calculation
            if direction == "LONG":
                pnl_pct = ((price - entry) / entry) * 100
                dist_to_tp = ((tp - price) / price) * 100
                dist_to_sl = ((price - sl) / price) * 100
            else:  # SHORT
                pnl_pct = ((entry - price) / entry) * 100
                dist_to_tp = ((price - tp) / price) * 100
                dist_to_sl = ((sl - price) / price) * 100
            
            pnl_emoji = "🟢" if pnl_pct >= 0 else "🔴"
            
            if trade_mode == "scalping":
                # Compact format for scalping
                order_text = (
                    f"⚡ *#{tid} {symbol}*{time_info} `{direction}`\n"
                    f"   {pnl_emoji} `{pnl_pct:+.2f}%` | TP `{dist_to_tp:+.2f}%` | SL `{dist_to_sl:+.2f}%`"
                )
            elif trade_mode == "metals_scalping":
                order_text = (
                    f"🥇 *#{tid} {symbol}*{time_info} `{direction}`\n"
                    f"   {pnl_emoji} `{pnl_pct:+.2f}%` | TP `{dist_to_tp:+.2f}%` | SL `{dist_to_sl:+.2f}%`"
                )
            elif trade_mode == "ai":
                # AI orders shown separately but with rich info similar to standard
                order_text = (
                    f"🤖 *#{tid} {symbol}* {in_monitor}`{direction}`{time_info}\n"
                    f"   💰 Price: `{price:.4f}` {pnl_emoji} `{pnl_pct:+.2f}%`\n"
                    f"   📍 E: `{entry:.4f}`\n"
                    f"   🎯 TP: `{tp:.4f}` → `{dist_to_tp:+.2f}%`\n"
                    f"   🛑 SL: `{sl:.4f}` → `{dist_to_sl:+.2f}%`"
                )
            else:
                order_text = (
                    f"▫️ *#{tid} {symbol}* {in_monitor}`{direction}`{time_info}\n"
                    f"   💰 Price: `{price:.4f}` {pnl_emoji} `{pnl_pct:+.2f}%`\n"
                    f"   📍 E: `{entry:.4f}`\n"
                    f"   🎯 TP: `{tp:.4f}` → `{dist_to_tp:+.2f}%`\n"
                    f"   🛑 SL: `{sl:.4f}` → `{dist_to_sl:+.2f}%`"
                )
        except Exception:
            if trade_mode == "scalping":
                order_text = (
                    f"⚡ *#{tid} {symbol}* {in_monitor}`{direction}`{time_info}\n"
                    f"   📍 E: `{entry:.4f}` | SL: `{sl:.4f}` | TP: `{tp:.4f}`\n"
                    f"   💰 Price: —"
                )
            elif trade_mode == "metals_scalping":
                order_text = (
                    f"🥇 *#{tid} {symbol}* {in_monitor}`{direction}`{time_info}\n"
                    f"   📍 E: `{entry:.4f}` | SL: `{sl:.4f}` | TP: `{tp:.4f}`\n"
                    f"   💰 Price: —"
                )
            elif trade_mode == "ai":
                order_text = (
                    f"🤖 *#{tid} {symbol}* {in_monitor}`{direction}`{time_info}\n"
                    f"   📍 E: `{entry:.4f}` | SL: `{sl:.4f}` | TP: `{tp:.4f}`\n"
                    f"   💰 Price: —"
                )
            else:
                order_text = (
                    f"▫️ *#{tid} {symbol}* {in_monitor}`{direction}`{time_info}\n"
                    f"   📍 E: `{entry:.4f}` | SL: `{sl:.4f}` | TP: `{tp:.4f}`\n"
                    f"   💰 Price: —"
                )
        
        if trade_mode == "scalping":
            scalping_orders.append(order_text)
        elif trade_mode == "metals_scalping":
            metals_orders.append(order_text)
        elif trade_mode == "ai":
            ai_orders.append(order_text)
        else:
            standard_orders.append(order_text)
    
    # Build result with explicit AI section
    lines = []
    
    if scalping_orders:
        lines.append(f"⚡ *SCALPING: {len(scalping_orders)}*\n")
        lines.extend(scalping_orders)

    if metals_orders:
        if scalping_orders:
            lines.append("\n" + "─" * 20 + "\n")
        lines.append(f"🥇 *METALS SCALPING: {len(metals_orders)}*\n")
        lines.extend(metals_orders)
    
    if ai_orders:
        if scalping_orders or metals_orders:
            lines.append("\n" + "─" * 20 + "\n")
        lines.append(f"🤖 *AI: {len(ai_orders)}*\n")
        lines.extend(ai_orders)
    
    if standard_orders:
        if scalping_orders or metals_orders or ai_orders:
            lines.append("\n" + "─" * 20 + "\n")
        lines.append(f"📊 *STANDARD: {len(standard_orders)}*\n")
        lines.extend(standard_orders)
    
    total = len(scalping_orders) + len(metals_orders) + len(ai_orders) + len(standard_orders)
    header = f"📦 *Відкриті ордери: {total}*\n\n"
    
    # Show monitored symbols if set
    if monitored_list:
        header += f"📋 Monitored: `{', '.join(monitored_list[:5])}`{'...' if len(monitored_list) > 5 else ''}\n\n"
    
    return header + "\n\n".join(lines)


def _orders_keyboard() -> InlineKeyboardMarkup:
    """Клавіатура для ордерів."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Оновити", callback_data="orders:refresh")],
        [
            InlineKeyboardButton("📈 KPI", callback_data="kpi:trades:7"),
            InlineKeyboardButton("🥇 Metals KPI", callback_data="metals_kpi:7"),
            InlineKeyboardButton("⚡ Scalp Stats", callback_data="scalp:stats"),
        ],
    ])


async def orders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /orders — показує список відкритих ордерів."""
    user_id = update.effective_user.id
    text = _get_open_orders(user_id)
    await update.effective_chat.send_message(text, reply_markup=_orders_keyboard(), parse_mode="Markdown")


async def orders_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback для оновлення списку ордерів."""
    q = update.callback_query
    await q.answer()
    
    user_id = q.from_user.id
    text = _get_open_orders(user_id)
    new_markup = _orders_keyboard()
    
    try:
        await q.edit_message_text(text=text, reply_markup=new_markup, parse_mode="Markdown")
    except Exception:
        # Message not modified
        pass


# ───────────────────────────────────────────────
# /scalp_stats command & callback ✨
# ───────────────────────────────────────────────
def _get_scalp_stats(user_id: int, days: int = 7) -> str:
    """Get scalping statistics."""
    from utils.db import get_conn
    from utils.user_settings import get_user_settings
    import time
    
    us = get_user_settings(user_id) if user_id else {}
    scalping_mode = int(us.get("scalping_mode") or 0)
    sl_pct = float(us.get("scalping_sl_pct") or 0.3)
    tp_pct = float(us.get("scalping_tp_pct") or 0.9)
    slippage = float(us.get("slippage_pct") or 0.05)
    monitored = us.get("monitored_symbols") or ""
    monitored_list = [s.strip().upper() for s in monitored.split(",") if s.strip()] if monitored else []
    
    t_now = int(time.time())
    t_start = t_now - days * 86400
    
    with get_conn() as conn:
        # Check if trade_mode column exists
        cols = [c[1] for c in conn.execute("PRAGMA table_info(trades)").fetchall()]
        has_mode = "trade_mode" in cols
        # Prefer pnl_usd if available, otherwise fall back to pnl
        has_pnl = "pnl_usd" in cols or "pnl" in cols
        pnl_col = "pnl_usd" if "pnl_usd" in cols else ("pnl" if "pnl" in cols else "0")
        direction_expr = "direction" if "direction" in cols else "'' AS direction"
        entry_expr = "entry" if "entry" in cols else "NULL AS entry"
        sl_expr = "sl" if "sl" in cols else "NULL AS sl"
        tp_expr = "tp" if "tp" in cols else "NULL AS tp"
        closed_expr = "closed_at" if "closed_at" in cols else "NULL AS closed_at"
        opened_col = "opened_at" if "opened_at" in cols else ("ts_created" if "ts_created" in cols else None)
        order_col = opened_col or "id"

        if opened_col == "opened_at":
            time_pred = (
                "((opened_at GLOB '[0-9]*' AND CAST(opened_at AS INTEGER) >= ?) "
                "OR opened_at >= datetime(?, 'unixepoch'))"
            )
            time_params = (t_start, t_start)
        elif opened_col == "ts_created":
            time_pred = "ts_created >= ?"
            time_params = (t_start,)
        else:
            time_pred = "1=1"
            time_params = tuple()

        if has_mode:
            # Scalping trades
            scalp_trades = conn.execute(f"""
                SELECT symbol, {direction_expr}, {entry_expr}, {sl_expr}, {tp_expr}, status, {pnl_col}, {opened_col or 'NULL'}, {closed_expr}
                FROM trades
                WHERE trade_mode='scalping' AND {time_pred}
                ORDER BY {order_col} DESC
            """, time_params).fetchall()
            
            # Standard trades for comparison
            std_trades = conn.execute(f"""
                SELECT symbol, {direction_expr}, {entry_expr}, {sl_expr}, {tp_expr}, status, {pnl_col}, {opened_col or 'NULL'}, {closed_expr}
                FROM trades
                WHERE (trade_mode='standard' OR trade_mode IS NULL) AND {time_pred}
                ORDER BY {order_col} DESC
            """, time_params).fetchall()

            ai_trades = conn.execute(f"""
                SELECT symbol, {direction_expr}, {entry_expr}, {sl_expr}, {tp_expr}, status, {pnl_col}, {opened_col or 'NULL'}, {closed_expr}
                FROM trades
                WHERE trade_mode='ai' AND {time_pred}
                ORDER BY {order_col} DESC
            """, time_params).fetchall()
        else:
            scalp_trades = []
            std_trades = conn.execute(f"""
                SELECT symbol, {direction_expr}, {entry_expr}, {sl_expr}, {tp_expr}, status, {pnl_col}, {opened_col or 'NULL'}, {closed_expr}
                FROM trades WHERE {time_pred}
                ORDER BY {order_col} DESC
            """, time_params).fetchall()
            ai_trades = []
    
    # Calculate scalping stats
    scalp_total = len(scalp_trades)

    def _is_win(t):
        st = t[5]
        pnl = float(t[6] or 0)
        return st == 'WIN' or (st == 'CLOSED' and pnl > 0)

    def _is_loss(t):
        st = t[5]
        pnl = float(t[6] or 0)
        return st == 'LOSS' or (st == 'CLOSED' and pnl <= 0)

    scalp_wins = sum(1 for t in scalp_trades if _is_win(t))
    scalp_losses = sum(1 for t in scalp_trades if _is_loss(t))
    scalp_open = sum(1 for t in scalp_trades if t[5] == 'OPEN')
    scalp_winrate = (scalp_wins / (scalp_wins + scalp_losses) * 100) if (scalp_wins + scalp_losses) > 0 else 0
    scalp_pnl = sum(float(t[6] or 0) for t in scalp_trades)

    # Standard stats
    std_total = len(std_trades)
    std_wins = sum(1 for t in std_trades if _is_win(t))
    std_losses = sum(1 for t in std_trades if _is_loss(t))
    std_winrate = (std_wins / (std_wins + std_losses) * 100) if (std_wins + std_losses) > 0 else 0
    std_pnl = sum(float(t[6] or 0) for t in std_trades)

    # AI trades (if present)
    ai_total = len(ai_trades)
    ai_wins = sum(1 for t in ai_trades if _is_win(t))
    ai_losses = sum(1 for t in ai_trades if _is_loss(t))
    ai_open = sum(1 for t in ai_trades if t[5] == 'OPEN')
    ai_winrate = (ai_wins / (ai_wins + ai_losses) * 100) if (ai_wins + ai_losses) > 0 else 0
    ai_pnl = sum(float(t[6] or 0) for t in ai_trades)
    
    # Symbols breakdown for scalping
    scalp_by_symbol = {}
    for t in scalp_trades:
        sym = t[0]
        if sym not in scalp_by_symbol:
            scalp_by_symbol[sym] = {"wins": 0, "losses": 0, "open": 0, "pnl": 0}
        if _is_win(t):
            scalp_by_symbol[sym]["wins"] += 1
        elif _is_loss(t):
            scalp_by_symbol[sym]["losses"] += 1
        elif t[5] == 'OPEN':
            scalp_by_symbol[sym]["open"] += 1
        scalp_by_symbol[sym]["pnl"] += float(t[6] or 0)
    
    # Build report
    mode_status = "✅ ON" if scalping_mode else "❌ OFF"

    # Format PnL depending on column used
    if pnl_col == 'pnl_usd':
        scalp_pnl_str = f"`{scalp_pnl:+.2f}$`"
        def _fmt_sym_pnl(v):
            return f"`{v:+.2f}$`"
        std_pnl_str = f"`{std_pnl:+.2f}$`"
        ai_pnl_str = f"`{ai_pnl:+.2f}$`"
    else:
        scalp_pnl_str = f"`{scalp_pnl:+.2f}%`"
        def _fmt_sym_pnl(v):
            return f"`{v:+.2f}%`"
        std_pnl_str = f"`{std_pnl:+.2f}%`"
        ai_pnl_str = f"`{ai_pnl:+.2f}%`"

    lines = [
        f"⚡ *SCALPING STATS ({days}d)*\n",
        f"Mode: {mode_status}",
        f"Settings: SL={sl_pct}% | TP={tp_pct}% | Slip={slippage}%\n",
        f"📊 *Performance:*",
        f"Trades: {scalp_total} (Closed:{scalp_wins+scalp_losses} W:{scalp_wins} L:{scalp_losses} Open:{scalp_open})",
        f"Winrate (closed only): `{scalp_winrate:.1f}%`",
        f"PnL: {scalp_pnl_str}\n",
    ]

    if scalp_by_symbol:
        lines.append("📋 *By Symbol:*")
        sorted_syms = sorted(scalp_by_symbol.items(), key=lambda x: x[1]["pnl"], reverse=True)
        for sym, data in sorted_syms[:8]:
            total = data["wins"] + data["losses"] + data["open"]
            closed = data["wins"] + data["losses"]
            wr = (data["wins"] / closed * 100) if closed > 0 else 0
            in_mon = "📍" if sym in monitored_list else ""
            lines.append(f"  {in_mon}`{sym}`: {total}T (Closed:{closed} W:{data['wins']} L:{data['losses']} Open:{data['open']}) → {_fmt_sym_pnl(data['pnl'])}")

    lines.append(f"\n{'─' * 25}")
    lines.append(f"📈 *STANDARD ({days}d):*")
    lines.append(f"Trades: {std_total} | WR: `{std_winrate:.1f}%` | PnL: {std_pnl_str}")
    
    if monitored_list:
        lines.append(f"\n📋 Monitored: `{', '.join(monitored_list[:5])}`")

    # AI section (if any AI trades in the timeframe)
    if ai_total:
        lines.append(f"\n{'─' * 25}")
        lines.append(f"🤖 *AI ({days}d):*")
        lines.append(f"Trades: {ai_total} (W:{ai_wins} L:{ai_losses} O:{ai_open})")
        lines.append(f"Winrate: `{ai_winrate:.1f}%`")
        lines.append(f"PnL: {ai_pnl_str}")

    return "\n".join(lines)


def _scalp_stats_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1д", callback_data="scalp:stats:1"),
            InlineKeyboardButton("7д", callback_data="scalp:stats:7"),
            InlineKeyboardButton("14д", callback_data="scalp:stats:14"),
            InlineKeyboardButton("30д", callback_data="scalp:stats:30"),
        ],
        [InlineKeyboardButton("📦 Orders", callback_data="orders:refresh")],
    ])


async def scalp_stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /scalp_stats — показує статистику скальпінгу."""
    user_id = update.effective_user.id
    days = 7
    if context.args:
        try:
            days = int(context.args[0])
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to convert value: {e}")
            pass
    text = _get_scalp_stats(user_id, days)
    await update.effective_chat.send_message(text, reply_markup=_scalp_stats_keyboard(), parse_mode="Markdown")


async def scalp_stats_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback для scalp:stats."""
    q = update.callback_query
    await q.answer()
    
    data = q.data or ""
    parts = data.split(":")
    days = 7
    if len(parts) >= 3:
        try:
            days = int(parts[2])
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to convert value: {e}")
            pass
    user_id = q.from_user.id
    text = _get_scalp_stats(user_id, days)
    
    try:
        await q.edit_message_text(text=text, reply_markup=_scalp_stats_keyboard(), parse_mode="Markdown")
    except Exception:
        pass


# ───────────────────────────────────────────────
# Symbols Editor (ConversationHandler)
# ───────────────────────────────────────────────
SYMBOLS_INPUT = 1

async def symbols_start_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start symbols editing - triggered by panel button."""
    q = update.callback_query
    log.info(f"[symbols] start_edit triggered, user={q.from_user.id}, data={q.data}")
    await q.answer()
    
    user_id = q.from_user.id
    from utils.user_settings import get_user_settings
    us = get_user_settings(user_id) or {}
    current = us.get("monitored_symbols") or ""
    
    if current:
        current_display = current
    else:
        from core_config import CFG
        current_display = CFG.get("monitored_symbols", "BTCUSDT,ETHUSDT,BNBUSDT")
        current_display += " (default)"
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Reset to Default", callback_data="symbols:reset")],
        [InlineKeyboardButton("❌ Cancel", callback_data="symbols:cancel")],
    ])
    
    await q.edit_message_text(
        f"📋 *Edit Monitored Symbols*\n\n"
        f"Current: `{current_display}`\n\n"
        f"Send new symbols separated by comma:\n"
        f"`BTC, ETH, BNB, SOL` or `BTCUSDT, ETHUSDT`\n\n"
        f"✓ Will validate on Binance.",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    log.info(f"[symbols] awaiting input from user={user_id}")
    return SYMBOLS_INPUT


async def symbols_receive_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive and validate symbols from user input."""
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    from utils.symbol_validator import validate_symbols
    from utils.user_settings import set_user_settings
    from telegram_bot.panel import panel_keyboard
    
    valid, invalid = validate_symbols(text)
    
    if not valid:
        await update.message.reply_text(
            f"No valid symbols found.\n"
            f"Invalid: {', '.join(invalid)}\n\n"
            f"Try again or /cancel"
        )
        return SYMBOLS_INPUT
    
    # Save valid symbols
    set_user_settings(user_id, monitored_symbols=",".join(valid))
    
    response = f"Saved {len(valid)} symbols:\n`{', '.join(valid)}`"
    if invalid:
        response += f"\n\nSkipped invalid: {', '.join(invalid)}"
    
    kb = panel_keyboard(user_id)
    await update.message.reply_text(response, reply_markup=kb, parse_mode="Markdown")
    return ConversationHandler.END


async def symbols_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Reset symbols to default."""
    q = update.callback_query
    await q.answer("Reset to default")
    
    user_id = q.from_user.id
    from utils.user_settings import set_user_settings
    from telegram_bot.panel import panel_keyboard
    
    set_user_settings(user_id, monitored_symbols="")
    
    kb = panel_keyboard(user_id)
    await q.edit_message_text("Symbols reset to default.", reply_markup=kb)
    return ConversationHandler.END


async def symbols_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel symbols editing."""
    q = update.callback_query
    await q.answer("Cancelled")
    
    user_id = q.from_user.id
    from telegram_bot.panel import panel_keyboard
    
    kb = panel_keyboard(user_id)
    await q.edit_message_text("Symbols editing cancelled.", reply_markup=kb)
    return ConversationHandler.END


# ───────────────────────────────────────────────
# Error handler
# ───────────────────────────────────────────────
async def on_error(update: object, context) -> None:
    if isinstance(context.error, asyncio.CancelledError):
        log.info("Task was cancelled (graceful): %s", context.error)
        return
    try:
        uid = getattr(getattr(update, "effective_user", None), "id", None)
        chat = getattr(getattr(update, "effective_chat", None), "id", None)
        log.exception(
            "Unhandled error (user=%s chat=%s): %s", uid, chat, context.error
        )
    except Exception:
        log.exception("Unhandled error while processing update", exc_info=context.error)


# ───────────────────────────────────────────────
# Jobs
# ───────────────────────────────────────────────
def _setting_bool_value(raw, default: bool = False) -> bool:
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _perf_epoch_ts() -> int | None:
    from utils.settings import get_setting
    raw = get_setting("autopost_perf_epoch_ts", os.getenv("AUTOPOST_PERF_EPOCH_TS", ""))
    if raw in (None, ""):
        return None
    try:
        value = int(float(raw))
        return value if value > 0 else None
    except Exception:
        return None


def _closed_epoch_filter(epoch_ts: int | None) -> tuple[str, tuple]:
    if not epoch_ts:
        return "", ()
    return (
        """
           AND (
                (closed_at GLOB '[0-9]*' AND CAST(closed_at AS INTEGER) >= ?)
                OR closed_at >= datetime(?, 'unixepoch')
           )
        """,
        (epoch_ts, epoch_ts),
    )


def _recent_autopost_wr(conn, window: int, epoch_ts: int | None = None, allow_partial: bool = False) -> tuple[int, int, float] | None:
    if window <= 0:
        return None
    epoch_sql, epoch_params = _closed_epoch_filter(epoch_ts)
    rows = conn.execute(
        f"""
        SELECT COALESCE(pnl_usd, pnl, 0) AS pnl
          FROM trades
         WHERE status!='OPEN'
         {epoch_sql}
         ORDER BY id DESC
         LIMIT ?
        """,
        (*epoch_params, window),
    ).fetchall()
    if len(rows) < window and not allow_partial:
        return None
    if not rows:
        return 0, 0, 0.0
    wins = sum(1 for row in rows if float(row["pnl"] if hasattr(row, "keys") else row[0]) > 0)
    wr = wins / len(rows)
    return wins, len(rows), wr


def _log_execution_decision(
    decision: str,
    reason: str,
    message=None,
    *,
    trade_id=None,
    risk_state: str | None = None,
) -> None:
    if not isinstance(message, dict):
        candidate = None
    else:
        candidate = message
    extra = reason
    if trade_id is not None:
        extra = f"{reason}; trade_id={trade_id}"
    log_decision(
        source="autopost_scan",
        decision=decision,
        reason=extra,
        candidate=candidate,
        trade_mode=(candidate or {}).get("trade_mode"),
        risk_state=risk_state,
        indicators=(candidate or {}).get("ind"),
    )


def _risk_bool_label(value: bool) -> str:
    return "on" if value else "off"


def _sql_value(row, key: str, index: int, default=None):
    try:
        return row[key]
    except Exception:
        try:
            return row[index]
        except Exception:
            return default


def _get_risk_snapshot() -> str:
    from utils.db import get_conn
    from utils.settings import get_setting

    low_window = int(get_setting("autopost_low_wr_window", "20") or 20)
    pause_min = float(get_setting("autopost_low_wr_pause_min", "0.20") or 0.20)
    recovery_min = float(get_setting("autopost_recovery_wr_min", get_setting("autopost_low_wr_min", "0.35")) or 0.35)
    low_wr_block = _setting_bool_value(get_setting("autopost_low_wr_block", "true"), True)
    recovery_enabled = _setting_bool_value(get_setting("autopost_recovery_enabled", "true"), True)
    disable_shorts = _setting_bool_value(get_setting("autopost_disable_shorts", os.getenv("AUTOPOST_DISABLE_SHORTS", "false")), False)
    metals_open = _setting_bool_value(get_setting("metals_autopost_open_trades", os.getenv("METALS_AUTOPOST_OPEN_TRADES", "false")), False)
    max_open_per_run = int(get_setting("max_open_per_run", "2") or 2)
    max_open_per_day = int(get_setting("max_open_per_day", "6") or 6)
    epoch_ts = _perf_epoch_ts()
    effective_max_open_per_run = max_open_per_run
    effective_max_open_per_day = max_open_per_day

    with get_conn() as conn:
        wr_state = _recent_autopost_wr(conn, low_window, epoch_ts, allow_partial=bool(epoch_ts))
        open_trades = int(
            conn.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN'").fetchone()[0] or 0
        )
        if epoch_ts:
            daily_opened = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM trades
                     WHERE opened_at >= datetime('now', '-24 hours')
                       AND (trade_mode IS NULL OR LOWER(trade_mode) != ?)
                       AND (
                            (opened_at GLOB '[0-9]*' AND CAST(opened_at AS INTEGER) >= ?)
                            OR opened_at >= datetime(?, 'unixepoch')
                       )
                    """,
                    (METALS_TRADE_MODE, epoch_ts, epoch_ts),
                ).fetchone()[0] or 0
            )
        else:
            daily_opened = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM trades
                     WHERE opened_at >= datetime('now', '-24 hours')
                       AND (trade_mode IS NULL OR LOWER(trade_mode) != ?)
                    """,
                    (METALS_TRADE_MODE,),
                ).fetchone()[0] or 0
            )
        metals_max_open_per_day = int(
            get_setting("metals_max_open_per_day", str(max_open_per_day)) or max_open_per_day
        )
        metals_daily_opened = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM trades
                 WHERE opened_at >= datetime('now', '-24 hours')
                   AND LOWER(COALESCE(trade_mode, '')) = ?
                """,
                (METALS_TRADE_MODE,),
            ).fetchone()[0] or 0
        )
        decision_where = """
            SELECT decision, COALESCE(reason, '') AS reason, COUNT(*) AS n
              FROM decision_log
             WHERE ts >= datetime('now', '-6 hours')
               AND decision IN (
                   'PAUSED', 'SHORT_DISABLED', 'SYMBOL_COOLDOWN', 'SYMBOL_BAN',
                   'GATE_FAIL', 'HARD_BLOCKERS', 'PROFIT_GUARD', 'RR_FAIL',
                   'SHORT_SIGNAL_ONLY', 'LONG_QUALITY', 'REGIME_FILTER', 'EV_FAIL',
                   'LIMIT_REACHED', 'ALREADY_OPEN', 'BRIDGE_SKIP'
               )
        """
        decision_params: tuple[object, ...] = ()
        if epoch_ts:
            decision_where += " AND ts >= datetime(?, 'unixepoch')"
            decision_params = (epoch_ts,)
        decision_rows = conn.execute(
            decision_where
            + """
             GROUP BY decision, reason
             ORDER BY n DESC, decision
             LIMIT 6
            """,
            decision_params,
        ).fetchall()

    if wr_state:
        wins, total, wr = wr_state
        epoch_note = " since reset" if epoch_ts else ""
        wr_text = f"{wins}/{total} = {wr:.0%}{epoch_note}"
        if epoch_ts and total < low_window:
            mode = "RECOVERY_WARMUP"
            if recovery_enabled:
                effective_max_open_per_run = min(
                    effective_max_open_per_run,
                    int(get_setting("autopost_recovery_max_open_per_run", "1") or 1),
                )
                effective_max_open_per_day = min(
                    effective_max_open_per_day,
                    int(get_setting("autopost_recovery_max_open_per_day", "2") or 2),
                )
        elif low_wr_block and wr < pause_min:
            mode = "PAUSED"
        elif recovery_enabled and wr < recovery_min:
            mode = "RECOVERY"
            effective_max_open_per_run = min(
                effective_max_open_per_run,
                int(get_setting("autopost_recovery_max_open_per_run", "1") or 1),
            )
            effective_max_open_per_day = min(
                effective_max_open_per_day,
                int(get_setting("autopost_recovery_max_open_per_day", "2") or 2),
            )
        else:
            mode = "NORMAL"
    else:
        wr_text = f"not enough closed trades for window {low_window}"
        mode = "WARMUP"

    block_lines = []
    for row in decision_rows:
        decision = _sql_value(row, "decision", 0, "")
        reason = _sql_value(row, "reason", 1, "")
        count = _sql_value(row, "n", 2, 0)
        reason_part = f" - {reason}" if reason else ""
        block_lines.append(f"- {decision}: {count}{reason_part}")
    if not block_lines:
        block_lines.append("- no recent blocks in decision_log")

    return "\n".join(
        [
            "Risk status",
            f"Mode: {mode}",
            f"Recent WR ({low_window}): {wr_text}",
            f"Pause threshold: {pause_min:.0%}",
            f"Recovery threshold: {recovery_min:.0%}",
            f"Performance reset: {'on' if epoch_ts else 'off'}",
            "",
            "Switches",
            f"AUTOPOST_DISABLE_SHORTS: {_risk_bool_label(disable_shorts)}",
            f"METALS_AUTOPOST_OPEN_TRADES: {_risk_bool_label(metals_open)}",
            f"Low-WR block: {_risk_bool_label(low_wr_block)}",
            f"Recovery mode: {_risk_bool_label(recovery_enabled)}",
            "",
            "Limits",
            f"Open trades now: {open_trades}",
            f"Crypto opened last 24h: {daily_opened}/{effective_max_open_per_day}",
            f"Metals opened last 24h: {metals_daily_opened}/{metals_max_open_per_day}",
            f"Max open per run: {effective_max_open_per_run}",
            f"Base crypto limits: run {max_open_per_run}, day {max_open_per_day}",
            "",
            "Top blocks, last 6h" + (" since reset" if epoch_ts else ""),
            *block_lines,
        ]
    )


async def risk_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        text = _get_risk_snapshot()
    except Exception as exc:
        log.warning("risk command failed: %s", exc, exc_info=True)
        text = f"Risk status unavailable: {exc}"
    await update.effective_chat.send_message(text)


def _get_why_snapshot(symbol: str) -> str:
    from utils.db import get_conn
    from utils.settings import get_setting
    from services.autopost.core import _symbol_risk_block_reason

    symbol = str(symbol or "").strip().upper()
    low_window = int(get_setting("autopost_low_wr_window", "20") or 20)
    pause_min = float(get_setting("autopost_low_wr_pause_min", "0.20") or 0.20)
    recovery_min = float(get_setting("autopost_recovery_wr_min", get_setting("autopost_low_wr_min", "0.35")) or 0.35)
    low_wr_block = _setting_bool_value(get_setting("autopost_low_wr_block", "true"), True)
    recovery_enabled = _setting_bool_value(get_setting("autopost_recovery_enabled", "true"), True)
    disable_shorts = _setting_bool_value(get_setting("autopost_disable_shorts", os.getenv("AUTOPOST_DISABLE_SHORTS", "false")), False)
    epoch_ts = _perf_epoch_ts()

    with get_conn() as conn:
        wr_state = _recent_autopost_wr(conn, low_window, epoch_ts, allow_partial=bool(epoch_ts))
        symbol_risk = _symbol_risk_block_reason(conn, symbol)
        open_rows = conn.execute(
            """
            SELECT id, direction, timeframe, entry, sl, tp, trade_mode, opened_at
              FROM trades
             WHERE symbol=? AND status='OPEN'
             ORDER BY id DESC
            """,
            (symbol,),
        ).fetchall()
        recent_rows = conn.execute(
            """
            SELECT id, direction, timeframe, COALESCE(pnl_usd, pnl, 0) AS pnl,
                   COALESCE(close_reason, reason_close, '') AS reason, closed_at, trade_mode
              FROM trades
             WHERE symbol=? AND status!='OPEN'
             ORDER BY id DESC
             LIMIT 12
            """,
            (symbol,),
        ).fetchall()
        last_trade = recent_rows[0] if recent_rows else None
        decision_rows = conn.execute(
            """
            SELECT ts, decision, COALESCE(reason, '') AS reason
              FROM decision_log
             WHERE symbol=?
             ORDER BY id DESC
             LIMIT 5
            """,
            (symbol,),
        ).fetchall()

    if wr_state:
        wins, total, wr = wr_state
        if epoch_ts and total < low_window:
            global_mode = f"RECOVERY_WARMUP ({wins}/{total}={wr:.0%} since reset)"
        elif low_wr_block and wr < pause_min:
            global_mode = f"PAUSED ({wins}/{total}={wr:.0%})"
        elif recovery_enabled and wr < recovery_min:
            global_mode = f"RECOVERY ({wins}/{total}={wr:.0%})"
        else:
            global_mode = f"NORMAL ({wins}/{total}={wr:.0%})"
    else:
        global_mode = f"WARMUP (need {low_window} closed trades)"

    wins_symbol = sum(1 for row in recent_rows if float(_sql_value(row, "pnl", 3, 0) or 0) > 0)
    pnl_symbol = sum(float(_sql_value(row, "pnl", 3, 0) or 0) for row in recent_rows)
    wr_symbol = (wins_symbol / len(recent_rows)) if recent_rows else None

    consecutive_sl = 0
    for row in recent_rows:
        if str(_sql_value(row, "reason", 4, "") or "").upper() == "SL":
            consecutive_sl += 1
        else:
            break

    lines = [
        f"Why {symbol}",
        f"Global mode: {global_mode}",
        f"SHORT auto-open: {'blocked' if disable_shorts else 'allowed'}",
        f"Symbol risk: {symbol_risk or 'no active symbol block'}",
        f"Consecutive SL: {consecutive_sl}",
        "",
        "Recent symbol stats",
    ]
    if recent_rows:
        lines.append(
            f"Closed sample: {wins_symbol}/{len(recent_rows)} wins"
            f" ({wr_symbol:.0%}), PnL {pnl_symbol:+.2f}"
        )
    else:
        lines.append("Closed sample: no closed trades for this symbol")

    lines.append("")
    lines.append("Open trades")
    if open_rows:
        for row in open_rows[:5]:
            lines.append(
                f"- #{_sql_value(row, 'id', 0)} {_sql_value(row, 'direction', 1)} {_sql_value(row, 'timeframe', 2)} "
                f"mode={_sql_value(row, 'trade_mode', 6) or '-'} "
                f"entry={_sql_value(row, 'entry', 3)} sl={_sql_value(row, 'sl', 4)} tp={_sql_value(row, 'tp', 5)}"
            )
    else:
        lines.append("- none")

    lines.append("")
    lines.append("Last closed trade")
    if last_trade:
        lines.append(
            f"#{_sql_value(last_trade, 'id', 0)} {_sql_value(last_trade, 'direction', 1)} "
            f"{_sql_value(last_trade, 'timeframe', 2)} "
            f"reason={_sql_value(last_trade, 'reason', 4) or '-'} "
            f"pnl={float(_sql_value(last_trade, 'pnl', 3, 0) or 0):+.2f} "
            f"mode={_sql_value(last_trade, 'trade_mode', 6) or '-'} "
            f"closed_at={_sql_value(last_trade, 'closed_at', 5) or '-'}"
        )
    else:
        lines.append("none")

    lines.append("")
    lines.append("Recent decision_log")
    if decision_rows:
        for row in decision_rows:
            row_reason = _sql_value(row, "reason", 2, "")
            reason = f" - {row_reason}" if row_reason else ""
            lines.append(f"- {_sql_value(row, 'ts', 0)} {_sql_value(row, 'decision', 1)}{reason}")
    else:
        lines.append("- no symbol-specific decisions yet")

    return "\n".join(lines)


async def why_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_chat.send_message("Usage: /why BTCUSDT")
        return
    symbol = context.args[0].strip().upper()
    if not symbol or len(symbol) > 20:
        await update.effective_chat.send_message("Usage: /why BTCUSDT")
        return
    try:
        text = _get_why_snapshot(symbol)
    except Exception as exc:
        log.warning("why command failed for %s: %s", symbol, exc, exc_info=True)
        text = f"Why {symbol} unavailable: {exc}"
    await update.effective_chat.send_message(text[:3900])


async def risk_report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        hours = int(context.args[0]) if context.args else 24
    except Exception:
        hours = 24
    try:
        text = build_daily_risk_report(hours=hours)
    except Exception as exc:
        log.warning("risk report command failed: %s", exc, exc_info=True)
        text = f"Risk report unavailable: {exc}"
    await update.effective_chat.send_message(text)


async def decision_report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        hours = int(context.args[0]) if context.args else 24
    except Exception:
        hours = 24
    try:
        text = build_decision_report(hours=hours)
    except Exception as exc:
        log.warning("decision report command failed: %s", exc, exc_info=True)
        text = f"Decision report unavailable: {exc}"
    await update.effective_chat.send_message(text)


async def paper_report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        hours = int(context.args[0]) if context.args else 168
    except Exception:
        hours = 168
    try:
        text = build_paper_report(hours=hours)
    except Exception as exc:
        log.warning("paper report command failed: %s", exc, exc_info=True)
        text = f"Paper report unavailable: {exc}"
    await update.effective_chat.send_message(text)


async def edge_report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        hours = int(context.args[0]) if context.args else 168
    except Exception:
        hours = 168
    try:
        text = build_edge_report(hours=hours)
    except Exception as exc:
        log.warning("edge report command failed: %s", exc, exc_info=True)
        text = f"Edge report unavailable: {exc}"
    await update.effective_chat.send_message(text)


async def open_risk_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        text = build_open_risk_report()
    except Exception as exc:
        log.warning("open risk command failed: %s", exc, exc_info=True)
        text = f"Open risk unavailable: {exc}"
    await update.effective_chat.send_message(text)


async def settings_audit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        text = build_settings_audit()
    except Exception as exc:
        log.warning("settings audit command failed: %s", exc, exc_info=True)
        text = f"Settings audit unavailable: {exc}"
    await update.effective_chat.send_message(text)


async def release_report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        hours = int(context.args[0]) if context.args else 168
    except Exception:
        hours = 168
    try:
        text = build_release_report(hours=hours, write_audit=True)
    except Exception as exc:
        log.warning("release report command failed: %s", exc, exc_info=True)
        text = f"Release report unavailable: {exc}"
    await update.effective_chat.send_message(text)


async def allowlist_proposal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        hours = int(context.args[0]) if context.args else 168
    except Exception:
        hours = 168
    try:
        text = build_allowlist_proposal(hours=hours, write_audit=True)
    except Exception as exc:
        log.warning("allowlist proposal command failed: %s", exc, exc_info=True)
        text = f"Allowlist proposal unavailable: {exc}"
    await update.effective_chat.send_message(text)


async def reset_perf_epoch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from utils.db import get_conn

    ts = int(time.time())
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO settings(key, value)
            VALUES('autopost_perf_epoch_ts', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (str(ts),),
        )
    log_decision(
        source="risk_control",
        decision="PERF_EPOCH_RESET",
        reason=f"autopost performance epoch reset to {ts}",
        risk_state="RECOVERY_WARMUP",
    )
    await update.effective_chat.send_message(
        "Performance epoch reset done.\n"
        "Old trades stay in DB for reports, but PAUSED/RECOVERY now count from this point.\n"
        "Bot starts in RECOVERY_WARMUP until enough closed trades are collected."
    )


async def autopost_scan(context) -> None:
    """Автопост: збирає сигнали, шле у TG, маркує sent та мостить у trades."""
    try:
        # універсально виконуємо run_autopost_once (async або sync)
        msgs = await _run_maybe_async(run_autopost_once, context.application)
        if not msgs:
            return

        default_chat = CFG.get("CHAT_ID") or CFG.get("TELEGRAM_CHAT_ID")

        # Rate limits and counters
        from utils.settings import get_setting
        max_open_per_run = int(get_setting("max_open_per_run", "2") or 2)
        max_open_per_day = int(get_setting("max_open_per_day", "6") or 6)
        recovery_mode = False
        recovery_max_messages = int(get_setting("autopost_recovery_max_messages_per_run", "1") or 1)
        opened_run = 0
        skipped_items = []  # list of (symbol,tf,reason)

        # Count opens in last 24h
        from utils.db import get_conn
        with get_conn() as conn:
            epoch_ts = _perf_epoch_ts()
            if epoch_ts:
                daily_opened = int(
                    conn.execute(
                        """
                        SELECT COUNT(*) FROM trades
                         WHERE opened_at >= datetime('now', '-24 hours')
                           AND (trade_mode IS NULL OR LOWER(trade_mode) != ?)
                           AND (
                                (opened_at GLOB '[0-9]*' AND CAST(opened_at AS INTEGER) >= ?)
                                OR opened_at >= datetime(?, 'unixepoch')
                           )
                        """,
                        (METALS_TRADE_MODE, epoch_ts, epoch_ts),
                    ).fetchone()[0] or 0
                )
            else:
                daily_opened = int(
                    conn.execute(
                        """
                        SELECT COUNT(*) FROM trades
                         WHERE opened_at >= datetime('now', '-24 hours')
                           AND (trade_mode IS NULL OR LOWER(trade_mode) != ?)
                        """,
                        (METALS_TRADE_MODE,),
                    ).fetchone()[0] or 0
                )
            if _setting_bool_value(get_setting("autopost_recovery_enabled", "true"), True):
                wr_window = int(get_setting("autopost_low_wr_window", "20") or 20)
                recovery_wr_min = float(get_setting("autopost_recovery_wr_min", get_setting("autopost_low_wr_min", "0.35")) or 0.35)
                state = _recent_autopost_wr(conn, wr_window, epoch_ts, allow_partial=bool(epoch_ts))
                if state:
                    wins, total, wr = state
                    if (epoch_ts and total < wr_window) or wr < recovery_wr_min:
                        recovery_mode = True
                        max_open_per_run = min(max_open_per_run, int(get_setting("autopost_recovery_max_open_per_run", "1") or 1))
                        max_open_per_day = min(max_open_per_day, int(get_setting("autopost_recovery_max_open_per_day", "2") or 2))
                        log_decision(
                            source="autopost_scan",
                            decision="RECOVERY",
                            reason=(
                                f"perf_epoch warmup {wins}/{total}/{wr_window}"
                                if epoch_ts and total < wr_window
                                else f"wr {wins}/{total}={wr:.0%} < recovery {recovery_wr_min:.0%}"
                            ),
                            trade_mode=(msgs[0].get("trade_mode") if msgs and isinstance(msgs[0], dict) else None),
                            risk_state="RECOVERY",
                            conn=conn,
                        )
                        log.info(
                            "[autopost] RECOVERY mode: wr %s/%s=%.0f%%, limits run=%s day=%s messages=%s",
                            wins, total, wr * 100, max_open_per_run, max_open_per_day, recovery_max_messages
                        )

        if recovery_mode and recovery_max_messages > 0:
            msgs = msgs[:recovery_max_messages]

        sent = 0
        seen: set[tuple[int | None, str]] = set()

        for m in msgs:
            if isinstance(m, str):
                text = m
                chat_id = default_chat
                parse_mode = constants.ParseMode.HTML
                btns = None
            else:
                text = (m.get("text", "") or "") if isinstance(m, dict) else ""
                chat_id = (m.get("chat_id") if isinstance(m, dict) else None) or default_chat
                parse_mode = m.get("parse_mode") if isinstance(m, dict) else constants.ParseMode.HTML
                btns = m.get("buttons") if isinstance(m, dict) else None

            if not text or not chat_id:
                _log_execution_decision("BRIDGE_SKIP", "empty_text_or_chat", m, risk_state="INVALID_MESSAGE")
                continue

            log.info(f"autopost sending to chat_id: {chat_id}")
            sig = (int(chat_id) if chat_id is not None else None, text)
            if sig in seen:
                _log_execution_decision("BRIDGE_SKIP", "duplicate_message_seen", m, risk_state="DEDUP")
                continue
            seen.add(sig)

            reply_markup = None
            if btns:
                rows = []
                for row in btns:
                    r = []
                    for b in row:
                        if b.get("type") == "url":
                            r.append(
                                InlineKeyboardButton(
                                    b.get("text", "Link"), url=b.get("url", "")
                                )
                            )
                        else:
                            # Guarantee callback_data is not empty and <64 chars
                            data = b.get("data") or "no_action"
                            if len(data) > 63:
                                # Shorten but keep unique info (symbol, tf, dir)
                                import re
                                m = re.match(r"panel:open_trade:([A-Z0-9]+):([a-z0-9]+):([A-Z]+)", data)
                                if m:
                                    symbol, tf, direction = m.groups()
                                    data = f"panel:open:{symbol}:{tf}:{direction}"
                                else:
                                    data = data[:63]
                            r.append(
                                InlineKeyboardButton(
                                    b.get("text", "…"), callback_data=data
                                )
                            )
                    if r:
                        rows.append(r)
                if rows:
                    reply_markup = InlineKeyboardMarkup(rows)

            # 1) Надіслати в TG
            send_ok = False
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=parse_mode,
                    disable_web_page_preview=True,
                    reply_markup=reply_markup,
                )
                sent += 1
                send_ok = True
                _log_execution_decision("SENT", "telegram_sent", m, risk_state="SENT")
            except Exception as e:
                log.warning("autopost send fail: %s", e)
                _log_execution_decision("BRIDGE_SKIP", f"telegram_send_failed: {e}", m, risk_state="SEND_FAILED")
            if not send_ok:
                continue

            # 2) Позначити «надіслано»
            try:
                if isinstance(m, dict):
                    mark_autopost_sent(
                        symbol=m.get("symbol"),
                        timeframe=m.get("timeframe"),
                        rr=m.get("rr"),
                    )
            except Exception as e:
                log.warning("autopost_log mark_sent fail: %s", e)

            # 3) Міст у trades + statuses (OPENED / ALREADY OPEN / SKIPPED)
            try:
                if isinstance(m, dict):
                    symbol = m.get("symbol")
                    timeframe = m.get("timeframe")

                    if m.get("signal_only"):
                        reason = m.get("signal_only_reason") or "signal_only"
                        _log_execution_decision("SIGNAL_ONLY", reason, m, risk_state="SIGNAL_ONLY")
                        try:
                            log.info("[autopost_status] %s", {"action":"SIGNAL_ONLY","symbol":symbol,"timeframe":timeframe,"reason":reason})
                        except Exception:
                            pass
                        try:
                            await context.bot.send_message(chat_id=chat_id, text=f"Status: SIGNAL ONLY ({reason})")
                        except Exception:
                            log.debug("failed to send signal-only status for %s/%s", symbol, timeframe)
                        skipped_items.append((symbol, timeframe, reason))
                        try:
                            from services.metrics import inc_skip
                            inc_skip()
                        except Exception:
                            pass
                        continue

                    # Enforce run/day limits
                    if opened_run >= max_open_per_run or (daily_opened + opened_run) >= max_open_per_day:
                        if opened_run >= max_open_per_run:
                            reason = f"run_limit {opened_run}/{max_open_per_run}"
                        else:
                            reason = f"day_limit {daily_opened + opened_run}/{max_open_per_day}"
                        if recovery_mode:
                            reason = f"recovery {reason}"
                        _log_execution_decision("LIMIT_REACHED", reason, m, risk_state="LIMIT_REACHED")
                        skip_text = (
                            "Status: SKIPPED (limit_reached)\n"
                            f"Reason: {reason}\n"
                            f"Limits: run {opened_run}/{max_open_per_run}, "
                            f"day {daily_opened + opened_run}/{max_open_per_day}"
                        )
                        try:
                            log.info("[autopost_status] %s", {"action":"SKIPPED","symbol":symbol,"timeframe":timeframe,"reason":reason})
                        except Exception:
                            pass
                        try:
                            await context.bot.send_message(chat_id=chat_id, text=skip_text)
                        except Exception:
                            log.debug("failed to send skip status for %s/%s", symbol, timeframe)
                        skipped_items.append((symbol, timeframe, reason))
                        try:
                            from services.metrics import inc_skip
                            inc_skip()
                        except Exception:
                            pass
                        continue

                    tid = handle_autopost_message(m)
                    if tid:
                        opened_run += 1
                        log.info("[autopost_scan] opened trade id=%s from message", tid)
                        _log_execution_decision("OPENED", "bridge_opened", m, trade_id=tid, risk_state="OPENED")
                        # Structured status log
                        try:
                            log.info("[autopost_status] %s", {"action":"OPENED","symbol":symbol,"timeframe":timeframe,"trade_id":tid})
                        except Exception:
                            pass
                        # Reset stable-pass counter for this candidate after opening
                        try:
                            from services.autopost.persistence import reset_candidate_pass
                            reset_candidate_pass(user_id=str(m.get('chat_id') or default_chat), symbol=symbol, timeframe=timeframe)
                        except Exception:
                            pass
                        try:
                            await context.bot.send_message(chat_id=chat_id, text=f"Status: OPENED (id={tid})")
                        except Exception:
                            log.debug("failed to send opened status for %s", symbol)
                        try:
                            from services.metrics import inc_open
                            inc_open()
                        except Exception:
                            pass
                    else:
                        # check if there is already an open trade
                        from utils.db import get_conn
                        with get_conn() as conn:
                            row = conn.execute("SELECT 1 FROM trades WHERE symbol=? AND timeframe=? AND status='OPEN' LIMIT 1", (symbol, timeframe)).fetchone()
                            if row:
                                status_text = "Status: ALREADY OPEN"
                                reason = "already_open"
                                decision = "ALREADY_OPEN"
                                risk_state = "ALREADY_OPEN"
                            else:
                                status_text = "Status: SKIPPED (bridge)"
                                reason = "bridge_skip"
                                decision = "BRIDGE_SKIP"
                                risk_state = "BRIDGE_SKIP"
                        _log_execution_decision(decision, reason, m, risk_state=risk_state)
                        try:
                            log.info("[autopost_status] %s", {"action":"SKIPPED","symbol":symbol,"timeframe":timeframe,"reason":reason})
                        except Exception:
                            pass
                        try:
                            await context.bot.send_message(chat_id=chat_id, text=status_text)
                        except Exception:
                            log.debug("failed to send status for %s", symbol)
                        skipped_items.append((symbol, timeframe, reason))
                        try:
                            from services.metrics import inc_skip
                            inc_skip()
                        except Exception:
                            pass
            except Exception as e:
                log.warning("autopost_bridge failed: %s", e)
                _log_execution_decision("BRIDGE_SKIP", f"bridge_exception: {e}", m, risk_state="BRIDGE_ERROR")
                try:
                    from services.metrics import inc_fail
                    inc_fail()
                except Exception:
                    pass

        # Summary: Opened / Skipped / Closed (recent)
        try:
            from utils.db import get_conn
            with get_conn() as conn:
                closed_count = int(conn.execute("SELECT COUNT(*) FROM trades WHERE closed_at >= datetime('now', '-00:10:00')").fetchone()[0] or 0)
        except Exception:
            closed_count = 0

        try:
            summary_text = f"Opened: {opened_run} • Skipped: {len(skipped_items)} • Closed (last 10m): {closed_count}"
            await context.bot.send_message(chat_id=default_chat, text=summary_text)
        except Exception:
            log.debug("failed to send autopost summary to %s", default_chat)

        log.info("autopost scan done (sent=%d)", sent)

    except Exception:
        log.warning("autopost_scan failed", exc_info=True)


async def signal_closer_job(context) -> None:
    """Періодичне закриття/NEUTRAL-обробка."""
    try:
        await _run_maybe_async(_close_fn)
    except Exception as e:
        log.warning("signal_closer failed: %s", e)


async def position_manager_job(context) -> None:
    """Partial TP / move SL to BE / легкий трейл."""
    try:
        updated = await _run_maybe_async(_pm_fn)
        if updated:
            log.info("position_manager: updated %d positions", updated)
    except Exception as e:
        log.warning("position_manager failed: %s", e)


async def daily_pnl_job(context) -> None:
    """Щоденний P&L о 23:59."""
    try:
        await _run_maybe_async(daily_tracker_job, context.bot)
    except Exception as e:
        log.warning("daily_pnl_job failed: %s", e)


async def winrate_daily_job(context) -> None:
    """Winrate за 7 днів о 00:05."""
    try:
        await _run_maybe_async(winrate_job, context.bot, 7)
    except Exception as e:
        log.warning("winrate_daily_job failed: %s", e)


async def signal_sync_job(context) -> None:
    """Синхронізація сигналів із джерел (якщо доступна)."""
    if sync_signals_once is None or str(os.getenv("SIGNAL_SYNC_ENABLED", "true")).lower() != "true":
        return
    try:
        await _run_maybe_async(sync_signals_once)
    except Exception as e:
        log.warning("signal_sync: %s", e)


async def alerts_job(context) -> None:
    """Risk alerts job."""
    try:
        await _run_maybe_async(_alerts_fn, context.bot)
    except Exception as e:
        log.warning("risk_alerts failed: %s", e)


async def metals_autopost_job(context) -> None:
    """Independent metals scalper autopost/open job."""
    try:
        msgs = await _run_maybe_async(run_metals_autopost_once)
        if not msgs:
            return
        chat_id = (
            os.getenv("METALS_AUTOPOST_CHAT_ID")
            or os.getenv("TELEGRAM_CHAT_ID")
            or CFG.get("tg_chat_id")
        )
        if not chat_id:
            log.warning("[metals] no chat id for autopost messages")
            return
        for msg in msgs:
            await context.bot.send_message(
                chat_id=chat_id,
                text=msg,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        log.info("[metals] autopost sent %d message(s)", len(msgs))
    except Exception:
        log.warning("metals_autopost_job failed", exc_info=True)


async def metals_closer_job(context) -> None:
    """Independent metals TP/SL closer."""
    try:
        closed = await _run_maybe_async(close_metals_trades_once)
        if closed:
            log.info("[metals] closer closed %d trade(s)", closed)
    except Exception:
        log.warning("metals_closer_job failed", exc_info=True)


async def paper_closer_job(context) -> None:
    try:
        closed = await _run_maybe_async(close_paper_signals_once)
        if closed:
            log.info("[paper] closer closed %d paper signal(s)", closed)
    except Exception:
        log.warning("paper_closer_job failed", exc_info=True)


async def allowlist_proposal_notify_job(context) -> None:
    try:
        hours = int(os.getenv("ALLOWLIST_PROPOSAL_LOOKBACK_HOURS", "168") or 168)
        state = get_allowlist_proposal_state(hours)
        key = str(state.get("key") or "")
        eligible = state.get("eligible") or []
        if not eligible or not key:
            return

        from utils.db import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key='allowlist_proposal_last_ready_key'"
            ).fetchone()
            last_key = str(row[0]) if row and row[0] is not None else ""
            if last_key == key:
                return
            conn.execute(
                """
                INSERT INTO settings(key, value)
                VALUES('allowlist_proposal_last_ready_key', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key,),
            )

        chat_id = CFG.get("CHAT_ID") or CFG.get("TELEGRAM_CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")
        if not chat_id:
            log.warning("[allowlist] eligible proposal ready but no chat_id configured")
            return
        text = build_allowlist_proposal(hours=hours, write_audit=True)
        await context.bot.send_message(chat_id=chat_id, text=text)
        log.info("[allowlist] proposal notification sent (%d eligible stream(s))", len(eligible))
    except Exception:
        log.warning("allowlist_proposal_notify_job failed", exc_info=True)


# ───────────────────────────────────────────────
# App bootstrap
# ───────────────────────────────────────────────
def build_app():

    if not CFG.get("tg_token"):
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing in .env")

    app = (
        Application.builder().token(CFG["tg_token"]).rate_limiter(AIORateLimiter()).build()
    )
    # Додаємо auto_close_tp_sl_job до job_queue
    interval_tp_sl = int(CFG.get("auto_close_tp_sl_interval_sec", 60))
    app.job_queue.run_repeating(
        auto_close_tp_sl_job, interval=interval_tp_sl, first=25, name="auto_close_tp_sl"
    )

    # ✨ /kpi + callback
    app.add_handler(CommandHandler("kpi", kpi_cmd))
    # General KPI callbacks (presets and table switch)
    app.add_handler(CallbackQueryHandler(kpi_cb, pattern=r"^kpi:(trades|signals):\d+$"))
    # Close KPI view
    app.add_handler(CallbackQueryHandler(kpi_cb, pattern=r"^kpi:close$"))
    # KPI breakdown callbacks
    app.add_handler(CallbackQueryHandler(kpi_break_cb, pattern=r"^kpi:break:(trades|signals):\d+(:[^:]+)?$"))

    # ✨ /orders + callback
    app.add_handler(CommandHandler("orders", orders_cmd))
    app.add_handler(CallbackQueryHandler(orders_cb, pattern=r"^orders:refresh$"))

    # ✨ /scalp_stats + callback
    app.add_handler(CommandHandler("scalp_stats", scalp_stats_cmd))
    app.add_handler(CallbackQueryHandler(scalp_stats_cb, pattern=r"^scalp:stats(:\d+)?$"))

    # Risk state snapshot
    app.add_handler(CommandHandler("risk", risk_cmd))
    app.add_handler(CommandHandler("why", why_cmd))
    app.add_handler(CommandHandler("risk_report", risk_report_cmd))
    app.add_handler(CommandHandler("decision_report", decision_report_cmd))
    app.add_handler(CommandHandler("paper_report", paper_report_cmd))
    app.add_handler(CommandHandler("edge_report", edge_report_cmd))
    app.add_handler(CommandHandler("open_risk", open_risk_cmd))
    app.add_handler(CommandHandler("settings_audit", settings_audit_cmd))
    app.add_handler(CommandHandler("release_report", release_report_cmd))
    app.add_handler(CommandHandler("allowlist_proposal", allowlist_proposal_cmd))
    app.add_handler(CommandHandler("reset_perf_epoch", reset_perf_epoch_cmd))

    # ✨ Symbols editor (ConversationHandler)
    symbols_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(symbols_start_edit, pattern=r"^panel:edit_symbols:")],
        states={
            SYMBOLS_INPUT: [
                MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, symbols_receive_input),
                CallbackQueryHandler(symbols_reset, pattern=r"^symbols:reset$"),
                CallbackQueryHandler(symbols_cancel, pattern=r"^symbols:cancel$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(symbols_cancel, pattern=r"^symbols:cancel$"),
            CommandHandler("cancel", symbols_cancel),
        ],
        per_user=True,
        per_chat=True,
        per_message=False,  # Track by user/chat only
    )
    app.add_handler(symbols_conv, group=-10)  # Higher priority
    # ✨ /help, /guide і callback для гайду
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("guide", cmd_guide))
    app.add_handler(CallbackQueryHandler(show_signal_guide, pattern=r"^guide:signal$"))

    # 1) Спочатку — специфічні хендлери
    panel_neutral.register(app)
    register_extra(app)

    # 2) Потім — універсальні/інші
    register_handlers(app)

    # ───────────────────────────────────────────────
    # ОЧИСНИК дублікатів /ai і MessageHandler з командними фільтрами
    # Залишається тільки наш cmd_ai; інші /ai — видаляються.
    # Також прибираємо generic MessageHandler-и, що ловлять командні апдейти.
    # ───────────────────────────────────────────────
    try:
        # 1) Прибрати всі інші CommandHandler('/ai'), окрім нашого cmd_ai
        for grp, handlers in list((app.handlers or {}).items()):
            keep: list = []
            for h in handlers:
                if isinstance(h, CommandHandler):
                    cmds = set(getattr(h, "commands", set()))
                    if "ai" in cmds and getattr(h.callback, "__name__", "") != "cmd_ai":
                        log.info(
                            "[handlers] removing duplicate CommandHandler /ai from %s",
                            getattr(h.callback, "__qualname__", h.callback),
                        )
                        continue
                keep.append(h)
            app.handlers[grp] = keep

        # 2) Прибрати MessageHandler-и, що ловлять будь-які команди (щоб не підхоплювали /ai)
        for grp, handlers in list((app.handlers or {}).items()):
            keep2: list = []
            for h in handlers:
                if isinstance(h, MessageHandler):
                    f = getattr(h, "filters", None)
                    try:
                        if f is not None and (
                            f == tg_filters.COMMAND or (hasattr(f, "__str__") and "COMMAND" in str(f))
                        ):
                            log.info(
                                "[handlers] removing generic MessageHandler COMMAND from %s",
                                getattr(h.callback, "__qualname__", h.callback),
                            )
                            continue
                    except Exception:
                        # Безпечний пропуск на випадок нестандартних фільтрів
                        pass
                keep2.append(h)
            app.handlers[grp] = keep2
    except Exception:
        log.warning("handlers cleanup failed", exc_info=True)

    # error handler
    app.add_error_handler(on_error)

    # ── Планування робіт ──
    # Dynamic interval for autopost: 60s for scalping, 300s for standard
    try:
        from utils.user_settings import get_user_settings
        scheduler_user_id = os.getenv("TELEGRAM_CHAT_ID", "0")
        scheduler_us = get_user_settings(int(scheduler_user_id)) if scheduler_user_id else {}
        scalping_mode = int((scheduler_us or {}).get("scalping_mode") or 0)
        autopost_interval = 60 if scalping_mode else 300
    except Exception:
        autopost_interval = 300
    
    app.job_queue.run_repeating(
        autopost_scan, interval=autopost_interval, first=10, name="autopost_scan"
    )
    log.info(f"[autopost] scheduled with interval={autopost_interval}s (scalping={bool(scalping_mode) if 'scalping_mode' in dir() else False})")

    interval_closer = int(CFG.get("signal_closer_interval_sec", 120))
    interval_pm = int(CFG.get("position_manager_interval_sec", 60))
    interval_sync = int(CFG.get("signal_sync_interval_sec", 60))
    alerts_interval = int(CFG.get("alerts_interval_sec", 300))

    if _close_fn:
        app.job_queue.run_repeating(
            signal_closer_job, interval=interval_closer, first=15, name="signal_closer"
        )
    if _pm_fn:
        app.job_queue.run_repeating(
            position_manager_job, interval=interval_pm, first=20, name="position_manager"
        )
    app.job_queue.run_repeating(
        paper_closer_job, interval=60, first=40, name="paper_closer"
    )
    allowlist_notify_enabled = str(os.getenv("ALLOWLIST_PROPOSAL_NOTIFY_ENABLED", "true")).lower() in ("1", "true", "yes", "on")
    allowlist_notify_interval = int(os.getenv("ALLOWLIST_PROPOSAL_NOTIFY_INTERVAL_SEC", "1800") or 1800)
    if allowlist_notify_enabled:
        app.job_queue.run_repeating(
            allowlist_proposal_notify_job,
            interval=allowlist_notify_interval,
            first=90,
            name="allowlist_proposal_notify",
        )

    app.job_queue.run_daily(
        daily_pnl_job, time=dtime(hour=23, minute=59, tzinfo=TZ), name="daily_pnl_job"
    )
    app.job_queue.run_daily(
        winrate_daily_job, time=dtime(hour=0, minute=5, tzinfo=TZ), name="winrate_job"
    )

    signal_sync_enabled = str(os.getenv("SIGNAL_SYNC_ENABLED", "true")).lower() == "true"
    if sync_signals_once and signal_sync_enabled:
        app.job_queue.run_repeating(
            signal_sync_job, interval=interval_sync, first=30, name="signal_sync"
        )

    if _alerts_fn:
        app.job_queue.run_repeating(
            alerts_job, interval=alerts_interval, first=45, name="risk_alerts"
        )

    metals_autopost_enabled = str(os.getenv("METALS_AUTOPOST_ENABLED", "true")).lower() in ("1", "true", "yes", "on")
    metals_interval = int(os.getenv("METALS_AUTOPOST_INTERVAL_SEC", "600") or 600)
    if metals_autopost_enabled:
        app.job_queue.run_repeating(
            metals_autopost_job,
            interval=metals_interval,
            first=35,
            name="metals_autopost",
        )
        app.job_queue.run_repeating(
            metals_closer_job,
            interval=60,
            first=50,
            name="metals_closer",
        )
        log.info("[metals] scheduled autopost=%ss closer=60s", metals_interval)

    # Backfill/sync signals job (keep signals table fresh for KPI)
    try:
        from tools.backfill_signals_from_trades import backfill_signals

        async def backfill_job(context):
            await _run_maybe_async(backfill_signals, 7, False)

        app.job_queue.run_repeating(backfill_job, interval=86400, first=60, name="backfill_signals")
        log.info("[backfill] scheduled every 24h")
    except Exception:
        log.warning("backfill scheduling failed", exc_info=True)

    tz_key = getattr(TZ, "key", "Europe/Kyiv")
    log.info(
        (
            "[jobqueue] scheduled: autopost %ss, signal_closer %ss%s; "
            "position_manager %ss%s; daily_pnl 23:59; winrate 00:05; "
            "paper_closer 60s; allowlist_notify %ss%s; signal_sync %ss%s; "
            "risk_alerts %ss%s; metals %ss%s (TZ=%s)"
        ),
        autopost_interval,
        interval_closer,
        "" if _close_fn else " (off)",
        interval_pm,
        "" if _pm_fn else " (off)",
        allowlist_notify_interval,
        "" if allowlist_notify_enabled else " (off)",
        interval_sync,
        "" if (sync_signals_once and signal_sync_enabled) else " (off)",
        alerts_interval,
        "" if _alerts_fn else " (off)",
        metals_interval,
        "" if metals_autopost_enabled else " (off)",
        tz_key,
    )
    return app


def main() -> None:
    # авто-міграції (один раз на старт)
    try:
        from utils.db_migrate import migrate_if_needed
        migrate_if_needed()
    except Exception as e:
        log.warning("migrate_if_needed skipped: %s", e)

    app = build_app()
    mode = str(CFG.get("bot_mode", "polling")).lower()

    if mode == "webhook" and CFG.get("webhook_url"):
        url = CFG["webhook_url"].rstrip("/") + "/" + CFG["tg_token"]
        port = int(CFG.get("port", 8080))
        log.info("Starting bot (webhook) on port %s …", port)
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=CFG["tg_token"],
            webhook_url=url,
            drop_pending_updates=True,
        )
    else:
        log.info("Starting bot (polling)…")
        app.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )

        # після graceful shutdown/рестарту — гарантуємо індекси
        from utils.db import get_conn
        try:
            from utils.db_migrate import ensure_indexes_and_triggers
            with get_conn() as conn:
                ensure_indexes_and_triggers(conn)
        except Exception as e:
            log.warning("ensure_indexes_and_triggers skipped: %s", e)


if __name__ == "__main__":
    main()
