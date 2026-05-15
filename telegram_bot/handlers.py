# telegram_bot/handlers.py
from __future__ import annotations

import asyncio, math, logging, json, re, os, time, sqlite3, uuid
import inspect
from typing import Optional, List, Tuple, Dict, Any
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram.ext import CallbackQueryHandler
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ContextTypes, Application,
    CommandHandler, CallbackQueryHandler, ApplicationHandlerStop
)

from core_config import CFG
from router.analyzer_router import pick_route
from utils.openrouter import chat_completion
from utils.ta_formatter import format_ta_report
from market_data.candles import get_ohlcv
from market_data.binance_rank import get_all_usdt_24h, get_top_by_quote_volume_usdt
from utils.news_fetcher import get_latest_news
from telegram_bot.panel import panel_keyboard, apply_panel_action, symbols_overview_text
from utils.user_settings import ensure_user_row, get_user_settings
from services.daily_tracker import compute_daily_summary
from services.autopost import run_autopost_once

log = logging.getLogger("tg.handlers")

# ───────────────────────────────
# LLM guard & response helpers
# ───────────────────────────────
def _llm_allowed() -> bool:
    """
    False, якщо LLM вимкнено:
      - ENV LLM_DISABLED=1 / true / yes / on
      - або sitecustomize.LLM_DISABLED == True
    """
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
    """
    Дістає текст із відповіді LLM незалежно від форми:
    - рядок
    - dict з 'content'
    - openai/openrouter-подібна структура: choices[0].message.content | choices[0].text
    """


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


async def _safe_send(bot, chat_id, text: str, parse_mode: str | None = "Markdown"):
    if not text:
        return
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
    except Exception as e:
        log.warning("send_message failed: %s", e)

# ──────────────────────────────────────────────────────────────────────────────
# DB
# ──────────────────────────────────────────────────────────────────────────────
_DB_PATH = (
    os.getenv("DB_PATH")
    or os.getenv("SQLITE_PATH")
    or os.getenv("DATABASE_PATH")
    or "storage/bot.db"
)

def _conn_local():
    os.makedirs(os.path.dirname(_DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

def _signals_columns() -> set[str]:
    try:
        with _conn_local() as c:
            cur = c.execute("PRAGMA table_info(signals)")
            return {row[1] for row in cur.fetchall()}
    except Exception:
        return set()

def _now_ts() -> int:
    return int(time.time())

# ──────────────────────────────────────────────────────────────────────────────
# Signals table: schema guard + universal saver
# ──────────────────────────────────────────────────────────────────────────────
def _ensure_signals_schema():
    """Додає відсутні колонки без падіння (SQLite ALTER TABLE ADD COLUMN)."""
    try:
        with _conn_local() as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(signals)")
            cols = {row[1] for row in cur.fetchall()}

            # Додаємо те, чого може не бути у старій БД
            needed = [
                ("tf", "TEXT"),
                ("source", "TEXT"),
                ("analysis_id", "TEXT"),
                ("snapshot_ts", "INTEGER"),
                ("size_usd", "REAL"),
                ("rr", "REAL"),
                ("status", "TEXT"),
                ("ts_created", "INTEGER"),
                ("ts_closed", "INTEGER"),
                ("pnl_pct", "REAL"),
                ("sl", "REAL"),
                ("tp", "REAL"),
                ("timeframe", "TEXT"),  # ← ДОДАЛИ для легасі-вставок
                ("details", "TEXT"),  # ← ДОДАЛИ, бо save_signal_open передає details
            ]
            for col, typ in needed:
                if col not in cols:
                    try:
                        cur.execute(f"ALTER TABLE signals ADD COLUMN {col} {typ}")
                    except Exception:
                        pass
    except Exception as e:
        logging.getLogger("tg.handlers").warning("_ensure_signals_schema failed: %s", e)

# Виконуємо перевірку схеми при імпорті модуля
_ensure_signals_schema()

def save_signal_open(*args, **kwargs) -> int:
    """
    Універсальний saver:
      - приймає або один dict (row), або kwargs
      - сам підлаштовує INSERT під наявні колонки в БД (PRAGMA table_info)
      - робить back-compat: timeframe -> tf; а також stop -> sl якщо в схемі є 'sl'
    Повертає lastrowid або 0 при помилці.
    """
    # Зібрати row
    if args and isinstance(args[0], dict):
        row = dict(args[0])
    else:
        row = dict(kwargs)

    # back-compat: 'timeframe' -> 'tf'
    if "tf" not in row and "timeframe" in row:
        row["tf"] = row.pop("timeframe")

    # Значення за замовчуванням
    row.setdefault("status", "OPEN")
    row.setdefault("source", "ai")
    row.setdefault("rr", None)
    now_ts = int(time.time())
    row.setdefault("ts_created", now_ts)
    row.setdefault("snapshot_ts", now_ts)
    row.setdefault("size_usd", 100.0)

    # Обов'язкові / типобезпечні поля
    row["user_id"]   = int(row.get("user_id", 0) or 0)
    row["symbol"]    = str(row.get("symbol", "") or "")
    row["tf"]        = str(row.get("tf", "") or "")
    row["direction"] = str(row.get("direction", "NEUTRAL") or "NEUTRAL")

    def _f(v, default=0.0):
        try:
            vv = float(v)
            if math.isnan(vv) or math.isinf(vv):
                return float(default)
            return vv
        except Exception:
            return float(default)

    # нормалізуємо числові
    entry = row.get("entry", None)
    stop  = row.get("stop",  None)
    take  = row.get("tp",    None)
    rr_val = row.get("rr",   None)

    row["entry"] = _f(entry, 0.0)
    row["stop"]  = _f(stop,  0.0)
    row["tp"]    = _f(take,  0.0)

    if rr_val is not None:
        try:
            rr_val = float(rr_val)
            if math.isnan(rr_val) or math.isinf(rr_val):
                rr_val = None
        except Exception:
            rr_val = None
    row["rr"] = rr_val

    row["analysis_id"] = str(row.get("analysis_id", "") or "")
    try:
        row["snapshot_ts"] = int(row.get("snapshot_ts") or now_ts)
    except Exception:
        row["snapshot_ts"] = now_ts
    row["size_usd"] = _f(row.get("size_usd"), 100.0)
    try:
        row["ts_created"] = int(row.get("ts_created") or now_ts)
    except Exception:
        row["ts_created"] = now_ts

    # Дізнаємось реальні колонки
    cols = _signals_columns()

    # Якщо в схемі є 'sl' (stop-loss), підставимо туди значення stop
    if "sl" in cols and "sl" not in row:
        row["sl"] = row.get("stop", 0.0)

    # Перевага фіксованому порядку, але вставляємо тільки наявні в БД
    preferred_order = [
        "user_id","source","symbol","tf","direction",
        "entry","stop","sl","tp","rr",
        "status","ts_created","analysis_id","snapshot_ts","size_usd"
    ]
    insert_cols = [c for c in preferred_order if c in cols]

    if not insert_cols:
        logging.getLogger("tg.handlers").warning(
            "save_signal_open: no matching columns to insert. Existing=%r", cols
        )
        return 0

    placeholders = ",".join("?" for _ in insert_cols)
    col_list = ",".join(insert_cols)
    values = [row.get(c, None) for c in insert_cols]

    try:
        with _conn_local() as conn:
            cur = conn.cursor()
            cur.execute(f"INSERT INTO signals ({col_list}) VALUES ({placeholders})", values)
            return int(cur.lastrowid or 0)
    except Exception as e:
        logging.getLogger("tg.handlers").warning("save_signal_open failed: %s | row=%r", e, row)
        return 0

# для сумісності зі старими імпортами/викликами
_save_signal_open = save_signal_open

# ──────────────────────────────────────────────────────────────────────────────
# UI
# ──────────────────────────────────────────────────────────────────────────────
def get_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["/top", "/analyze", "/ai"],
            ["/req", "/symbols", "/metals"],
            ["/metals_scalp", "/guide", "/panel"],
            ["/kpi", "/ls", "/orders"],
        ],
        resize_keyboard=True
    )

def symbols_keyboard(user_id: int = None) -> InlineKeyboardMarkup:
    from utils.user_settings import get_user_settings
    # Try to get user's monitored_symbols first
    symbols = []
    if user_id:
        us = get_user_settings(user_id)
        if us and us.get("monitored_symbols"):
            symbols = [s.strip().upper() for s in us["monitored_symbols"].split(",") if s.strip()]
    
    # Fallback to CFG symbols
    if not symbols:
        symbols = [s.strip().upper() for s in CFG.get("symbols", []) if s.strip()]
    
    rows: list[list[InlineKeyboardButton]] = []
    if symbols:
        chunk = 4
        for i in range(0, len(symbols), chunk):
            group = symbols[i:i+chunk]
            rows.append([InlineKeyboardButton(text=s, callback_data=f"sym:{s}") for s in group])
    rows.append([
        InlineKeyboardButton("▶️ Analyze ALL", callback_data="an_all"),
        InlineKeyboardButton("🔄 Refresh", callback_data="an_refresh"),
        InlineKeyboardButton("⚙️ Panel", callback_data="goto_panel"),
    ])
    return InlineKeyboardMarkup(rows)

# ──────────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────────
_VALID_DIR_WORDS = {"LONG", "SHORT", "NEUTRAL"}

def _current_ai_model() -> str:
    try:
        probe = (CFG["symbols"][0] if CFG["symbols"] else "BTCUSDT")
        route = pick_route(probe)
        return route.model if route else "unknown"
    except Exception:
        return "unknown"

def _looks_like_symbol(s: str) -> bool:
    s = (s or "").strip().upper()
    if not (2 <= len(s) <= 20): return False
    if not all(c.isalnum() for c in s): return False
    for q in ("USDT", "FDUSD", "USDC", "BUSD", "BTC", "ETH", "EUR", "TRY"):
        if s.endswith(q): return True
    return False

def _pick_default_symbol() -> str:
    try:
        for x in CFG["symbols"]:
            x = (x or "").strip().upper()
            if _looks_like_symbol(x): return x
    except Exception:
        pass
    return "BTCUSDT"

def _parse_ai_json(txt: str) -> dict:
    try:
        t = (txt or "").strip()
        if t.startswith("```"):
            t = t.strip("` \n")
            t = re.sub(r"^json\s*", "", t, flags=re.I)
            t = re.sub(r"\s*json$", "", t, flags=re.I)
            t = t.strip("` \n")
        data = json.loads(t)
        return {
            "direction": str(data.get("direction","")).upper(),
            "entry": float(data.get("entry","nan")),
            "stop": float(data.get("stop","nan")),
            "tp": float(data.get("tp","nan")),
            "confidence": float(data.get("confidence",0.0)),
            "holding_time_hours": float(data.get("holding_time_hours",0.0)),
            "holding_time": str(data.get("holding_time","")).strip(),
            "rationale": str(data.get("rationale","")).strip(),
        }
    except Exception:
        dir_m = re.search(r"\b(LONG|SHORT|NEUTRAL)\b", txt or "", re.I)
        def num(rx):
            m = re.search(rx + r"\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)", txt or "", re.I)
            return float(m.group(1)) if m else float("nan")
        return {
            "direction": dir_m.group(1).upper() if dir_m else "NEUTRAL",
            "entry": num(r"(?:entry|price)"),
            "stop": num(r"(?:stop(?:-|\s*)loss|sl)"),
            "tp":   num(r"(?:take(?:-|\s*)profit|tp)"),
            "confidence": 0.5,
            "holding_time_hours": 0.0,
            "holding_time": "",
            "rationale": (txt or "").strip()
        }

def _fmt_or_dash(v):
    try:
        return f"{float(v):.2f}"
    except Exception:
        return "-"

def _compute_rr_num(direction: str, entry: float, stop: float, tp: float) -> Optional[float]:
    try:
        if any(math.isnan(x) for x in [entry, stop, tp]): return None
        if direction == "LONG":
            risk = entry - stop; reward = tp - entry
        elif direction == "SHORT":
            risk = stop - entry; reward = entry - tp
        else:
            return None
        if risk <= 0 or reward <= 0: return None
        return float(reward / risk)
    except Exception:
        return None

def _safe_float(x) -> Optional[float]:
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v): return None
        return v
    except Exception:
        return None

def _chunk(lst: List[str], n: int) -> List[List[str]]:
    return [lst[i:i+n] for i in range(0, len(lst), n)]

_MD_RX = re.compile(r"[*`]|(?<!`)_(?!`)")
def _strip_md(s: str) -> str:
    s = _MD_RX.sub("", s or "")
    s = re.sub(r"[^\S\r\n]+", " ", s).strip()
    return s

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

# ──────────────────────────────────────────────────────────────────────────────
# Import handlers from submodules (backward compatibility)
# ──────────────────────────────────────────────────────────────────────────────
# Note: Import after DB functions are defined to avoid circular dependencies
# All command handlers are now in handlers/ submodules

# These will be imported after DB setup
def _import_handlers():
    """Import handlers from submodules."""
    from telegram_bot.handlers.commands import start, help_cmd, guide, ping, news, get_keyboard
    from telegram_bot.handlers.panel_handlers import panel, on_cb_panel
    from telegram_bot.handlers.top_handlers import top, on_cb_sym, on_cb_topmode
    from telegram_bot.handlers.ai_commands import (
        req, analyze, on_cb_analyze_all, on_cb_an_refresh, on_cb_goto_panel,
        cmd_ai, on_cb_ai, on_cb_indicators, on_cb_dep, symbols_keyboard
    )
    from telegram_bot.handlers.kpi_handlers import daily_now, winrate_now
    from telegram_bot.handlers.callbacks import autopost_now
    return {
        "start": start, "help_cmd": help_cmd, "guide": guide, "ping": ping, "news": news,
        "get_keyboard": get_keyboard, "panel": panel, "on_cb_panel": on_cb_panel,
        "top": top, "on_cb_sym": on_cb_sym, "on_cb_topmode": on_cb_topmode,
        "req": req, "analyze": analyze, "on_cb_analyze_all": on_cb_analyze_all,
        "on_cb_an_refresh": on_cb_an_refresh, "on_cb_goto_panel": on_cb_goto_panel,
        "cmd_ai": cmd_ai, "on_cb_ai": on_cb_ai, "on_cb_indicators": on_cb_indicators,
        "on_cb_dep": on_cb_dep, "symbols_keyboard": symbols_keyboard,
        "daily_now": daily_now, "winrate_now": winrate_now, "autopost_now": autopost_now,
    }

# Import handlers after DB setup
_handlers_dict = _import_handlers()
locals().update(_handlers_dict)

# Re-export for backward compatibility
start = _handlers_dict["start"]
help_cmd = _handlers_dict["help_cmd"]
guide = _handlers_dict["guide"]
ping = _handlers_dict["ping"]
news = _handlers_dict["news"]
panel = _handlers_dict["panel"]
on_cb_panel = _handlers_dict["on_cb_panel"]
top = _handlers_dict["top"]
on_cb_sym = _handlers_dict["on_cb_sym"]
on_cb_topmode = _handlers_dict["on_cb_topmode"]
req = _handlers_dict["req"]
analyze = _handlers_dict["analyze"]
on_cb_analyze_all = _handlers_dict["on_cb_analyze_all"]
on_cb_an_refresh = _handlers_dict["on_cb_an_refresh"]
on_cb_goto_panel = _handlers_dict["on_cb_goto_panel"]
cmd_ai = _handlers_dict["cmd_ai"]
on_cb_ai = _handlers_dict["on_cb_ai"]
on_cb_indicators = _handlers_dict["on_cb_indicators"]
on_cb_dep = _handlers_dict["on_cb_dep"]
daily_now = _handlers_dict["daily_now"]
winrate_now = _handlers_dict["winrate_now"]
autopost_now = _handlers_dict["autopost_now"]
get_keyboard = _handlers_dict["get_keyboard"]
symbols_keyboard = _handlers_dict["symbols_keyboard"]

# ──────────────────────────────────────────────────────────────────────────────
# Legacy function definitions removed - now imported from submodules above
# Note: DB functions (save_signal_open, _conn_local, etc.) remain here as they're
# used by submodules. Command handlers are imported from handlers/ submodules.
# ──────────────────────────────────────────────────────────────────────────────
    text = (
        "🆘 *Довідка*\n\n"
        "Доступні команди:\n"
        "• `/top` — Топ-20 USDT пар (Volume / Gainers). Натисни на монету → меню дій (*🤖 AI*, *🔗 Залежність*).\n"
        f"• `/analyze` — Плитка монет з `MONITORED_SYMBOLS` (TF={CFG['analyze_timeframe']}) або *Analyze ALL*.\n"
        "• `/symbols` — Повний список монет моніторингу.\n"
        "• `/metals [TF]` — Окремий блок золото/срібло.\n"
        "• `/metals_scalp [TF]` — Скальпер по цінних металах.\n"
        "• `/ai <SYMBOL> [TF]` — AI-план (Entry/SL/TP, RR, утримання) + індикатори.\n"
        "• `/req <SYMBOL> [TF]` — Залежність монети від BTC/ETH (ρ, β, Δ Ratio).\n"
        "• `/news [запит]` — Останні заголовки.\n"
        "• `/panel` — Панель налаштувань.\n\n"
        "🛠 *Що нового*\n"
        "• Персональний TF у налаштуваннях: кожен користувач працює на своєму TF.\n"
        "• Автопост: ON/OFF, TF автопосту, RR-поріг.\n"
        "• Безпечна відправка: там, де можливий «нечистий» текст від моделей — без Markdown.\n\n"
        f"🧠 Модель: `{_current_ai_model()}`\n"
        f"⏱ TZ: `{CFG['tz']}`\n\n"
        "📖 *Гайд*\n"
        "1) `/panel` → обери *Timeframe* — дефолтний для `/ai`, `/req` тощо.\n"
        "2) Автопост у панелі:\n"
        "   • `Autopost` вкл/викл\n"
        "   • `Autopost TF` — окремий TF для фонового аналізу\n"
        "   • `Autopost RR` — мінімальний Risk/Reward для автопосту\n"
        "3) Якщо в повідомленні моделі трапляються посилання/символи — бот відправляє *без* Markdown.\n"
    )
    await _send(update, context, text, parse_mode="Markdown")

async def guide(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    await _send(update, context, f"🏓 pong all ok | AI model: {_current_ai_model()}")

# ──────────────────────────────────────────────────────────────────────────────
# /req — залежність BTC/ETH → <SYMBOL> [TF]
# ──────────────────────────────────────────────────────────────────────────────
def _pct(series: List[float]) -> List[float]:
    out = []
    for i in range(1, len(series)):
        prev = series[i-1] or 0.0
        out.append(0.0 if prev == 0 else (series[i]-series[i-1]) / prev)
    return out

def _corr(a: List[float], b: List[float]) -> float:
    import statistics as st
    n = min(len(a), len(b))
    if n < 3:
        return float("nan")
    a, b = a[:n], b[:n]
    try:
        ma, mb = st.mean(a), st.mean(b)
        cov = sum((x-ma)*(y-mb) for x,y in zip(a,b)) / (n-1)
        va = sum((x-ma)**2 for x in a) / (n-1)
        vb = sum((y-mb)**2 for y in b) / (n-1)
        if va <= 0 or vb <= 0:
            return float("nan")
        return cov / (va**0.5 * vb**0.5)
    except Exception:
        return float("nan")

def _beta(dep: List[float], indep: List[float]) -> float:
    import statistics as st
    n = min(len(dep), len(indep))
    if n < 3:
        return float("nan")
    dep, indep = dep[:n], indep[:n]
    md, mi = st.mean(dep), st.mean(indep)
    cov = sum((x-md)*(y-mi) for x,y in zip(dep,indep)) / (n-1)
    var_i = sum((y-mi)**2 for y in indep) / (n-1)
    if var_i <= 0:
        return float("nan")
    return cov / var_i

def _fmt(x, d=3, dash="-"):
    try:
        v = float(x)
        if v != v:  # NaN
            return dash
        return f"{v:.{d}f}"
    except Exception:
        return dash

async def _dependency_report(symbol: str, timeframe: str, limit: int = 300) -> str:
    """Рахує ρ/β до BTC/ETH та Δ ratio; повертає Markdown блок."""
    t_data = get_ohlcv(symbol, timeframe, limit)
    b_data = get_ohlcv("BTCUSDT", timeframe, limit)
    e_data = get_ohlcv("ETHUSDT", timeframe, limit)
    if not t_data or not b_data or not e_data:
        return "_No data to compute dependency_"

    t_close = [x["close"] for x in t_data]
    b_close = [x["close"] for x in b_data]
    e_close = [x["close"] for x in e_data]

    t_ret = _pct(t_close); b_ret = _pct(b_close); e_ret = _pct(e_close)

    win30 = 30 if len(t_ret) >= 30 else len(t_ret)
    win90 = 90 if len(t_ret) >= 90 else len(t_ret)

    corr_btc_30 = _corr(t_ret[-win30:], b_ret[-win30:])
    corr_eth_30 = _corr(t_ret[-win30:], e_ret[-win30:])
    corr_btc_90 = _corr(t_ret[-win90:], b_ret[-win90:])
    corr_eth_90 = _corr(t_ret[-win90:], e_ret[-win90:])

    beta_btc = _beta(t_ret[-win90:], b_ret[-win90:])
    beta_eth = _beta(t_ret[-win90:], e_ret[-win90:])

    ratio_btc_change = (
        (t_close[-1] / b_close[-1]) / (t_close[-win30] / b_close[-win30]) - 1
        if win30 >= 2 else float("nan")
    )
    ratio_eth_change = (
        (t_close[-1] / e_close[-1]) / (t_close[-win30] / e_close[-win30]) - 1
        if win30 >= 2 else float("nan")
    )

    # Короткий коментар за евристикою (3 пункти максимум)
    tips = []
    hi = lambda x: (isinstance(x, (int, float)) and x == x and x >= 0.6)
    lo = lambda x: (isinstance(x, (int, float)) and x == x and x < 0.3)
    if hi(corr_btc_30) or hi(corr_eth_30):
        tips.append(f"{symbol}: висока короткострокова кореляція з лідерами — рух синхронний, системний ризик.")
    if hi(beta_btc) or hi(beta_eth):
        tips.append(f"{symbol}: β>1 — амплітуда більша за лідера, тренд і ризик підсилюються.")
    if lo(corr_btc_90) and lo(corr_eth_90):
        tips.append(f"{symbol}: низька довгострокова кореляція — власні драйвери, диверсифікаційний ефект.")
    if ratio_btc_change == ratio_btc_change:
        tips.append(f"{symbol}: відносно BTC за 30 барів {ratio_btc_change*100:+.2f}% (сила/слабкість).")
    if ratio_eth_change == ratio_eth_change:
        tips.append(f"{symbol}: відносно ETH за 30 барів {ratio_eth_change*100:+.2f}% (друге підтвердження).")
    if not tips:
        tips = [f"{symbol}: звʼязок із BTC/ETH помірний; стеж за трендом (EMA/ADX) та обʼємами."]
    tips = tips[:3]

    md = []
    md.append(f"🔗 *Залежність BTC/ETH для* `{symbol}` *(TF={timeframe})*")
    md.append("")
    md.append(f"- ρ BTC (30/90): `{_fmt(corr_btc_30)}` / `{_fmt(corr_btc_90)}`")
    md.append(f"- ρ ETH (30/90): `{_fmt(corr_eth_30)}` / `{_fmt(corr_eth_90)}`")
    md.append(f"- β до BTC/ETH:  `{_fmt(beta_btc)}` / `{_fmt(beta_eth)}`")
    md.append(f"- Δ Ratio vs BTC (30): `{_fmt(ratio_btc_change*100,2)}%`")
    md.append(f"- Δ Ratio vs ETH (30): `{_fmt(ratio_eth_change*100,2)}%`")
    md.append("")
    md.append("🧠 *Коментар*:")
    md.append(("- " + tips[0])[:1200])
    return "\n".join(md)

async def req(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда: /req <SYMBOL> [TF] — кореляції/β/ratio до BTC та ETH."""
    uid = update.effective_user.id if update.effective_user else None
    us = get_user_settings(uid) if uid else {}
    user_tf = (us.get("timeframe") or CFG.get("analyze_timeframe") or "1h").strip()

    args = context.args or []
    symbol = (args[0] if args else _pick_default_symbol()).strip().upper()
    tf = (args[1] if len(args) > 1 else user_tf).strip()

    if not _looks_like_symbol(symbol):
        await _send(update, context, "⚠️ Приклад: `/req ADAUSDT 1h`", parse_mode="Markdown")
        return

    await _send(update, context, f"⏳ Рахую залежність BTC/ETH для {symbol} (TF={tf})…")
    try:
        report = await _dependency_report(symbol, tf, limit=300)
        await _send(update, context, report, parse_mode="Markdown")
    except Exception as e:
        log.exception("/req failed")
        await _send(update, context, f"⚠️ req error: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# /panel + callback
# ──────────────────────────────────────────────────────────────────────────────
async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Відкрити панель налаштувань для поточного користувача."""
    try:
        uid = update.effective_user.id
        chat_id = update.effective_chat.id
        log.info(f"/panel called by user {uid} in chat {chat_id}")
        ensure_user_row(uid)  # гарантуємо наявність рядка у user_settings
        kb = panel_keyboard(uid)
        await _send(update, context, "Панель налаштувань:", reply_markup=kb)
    except Exception as e:
        log.exception("/panel failed")
        await _send(update, context, f"⚠️ panel error: {e}")

async def on_cb_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка всіх натискань на кнопки панелі (toggle Autopost/TF/RR/Locale/Model...)."""
    q = update.callback_query
    try:
        await q.answer()
        data = (q.data or "")
        if not data.startswith("panel:"):
            return

        # формат callback_data: "panel:<action>:<value>"
        _p, action, value = (data.split(":", 2) + ["", ""])[0:3]
        uid = q.from_user.id

        ensure_user_row(uid)
        apply_panel_action(uid, action, value)

        if action == "show_symbols":
            await _send(
                update, context,
                symbols_overview_text(uid),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⬅️ Panel", callback_data="goto_panel"),
                    InlineKeyboardButton("✏️ Edit", callback_data="panel:edit_symbols:"),
                ]])
            )
            return

        if action == "help":
            await _send(
                update, context,
                "ℹ️ *Панель*\n"
                "- Autopost — вкл/викл фоновий аналіз моніторинг-пар.\n"
                "- TF — твій дефолтний таймфрейм (для /ai, /req тощо).\n"
                "- AP TF — таймфрейм автопосту.\n"
                "- AP RR — мінімальний Risk/Reward для автопосту.\n"
                "- Model — 'auto' або конкретна модель зі слотів.\n"
                "- Locale — мова відповідей (UK/EN).\n"
                "- Daily/Winrate — щоденний P&L і тижневий winrate.\n",
                parse_mode="Markdown"
            )

        # перерисувати клавіатуру
        try:
            await q.edit_message_reply_markup(panel_keyboard(uid))
        except Exception:
            await _send(update, context, "Панель налаштувань:", reply_markup=panel_keyboard(uid))

    except Exception as e:
        log.exception("on_cb_panel failed")
        await _send(update, context, f"⚠️ panel cb error: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# All command handlers are now in handlers/ submodules
# Keeping imports above for backward compatibility
# ──────────────────────────────────────────────────────────────────────────────
    q = update.callback_query
    await q.answer()
    try:
        uid = q.from_user.id if q.from_user else None
        us = get_user_settings(uid) if uid else {}
        user_tf = (us.get("timeframe") or CFG["analyze_timeframe"]).strip()
        user_locale = (us.get("locale") or CFG.get("default_locale", "uk")).strip().lower()
        if user_locale not in ("uk", "ua", "en"):
            user_locale = "uk"

        await _send(update, context, f"⏳ Аналізую всі монети на TF={user_tf}…")

        # єдиний analysis_id + snapshot_ts на весь батч (для консистентності)
        analysis_id = uuid.uuid4().hex
        snapshot_ts = int(time.time())
        size_usd = float(CFG.get("kpi_size_usd", 100.0))

        for symbol in CFG["symbols"]:
            try:
                symbol = (symbol or "").strip().upper()
                if not symbol:
                    continue

                data = get_ohlcv(symbol, user_tf, CFG["analyze_limit"])
                last_close = data[-1]["close"] if data else float("nan")

                block = [
                    f"SYMBOL: {symbol}",
                    f"TF: {user_tf}",
                    f"PRICE_LAST: {last_close:.6f}",
                    f"BARS: {min(len(data) if data else 0, CFG['analyze_limit'])}",
                ]
                user_model_key = (us.get("model_key") or "auto")
                route = pick_route(symbol, user_model_key=user_model_key)
                if not route:
                    await _send(update, context, f"❌ Немає доступного API-роутингу для {symbol}")
                    continue

                def _strip_md_local(s: str) -> str:
                    s = re.sub(r"[*_`]", "", s or "")
                    s = re.sub(r"[^\S\r\n]+", " ", s).strip()
                    return s

                # 12 індикаторів — беремо повний markdown і окремо «сирий» для prompt
                ta_block_full = format_ta_report(symbol, user_tf, CFG["analyze_limit"])
                ta_block_raw = _strip_md_local(ta_block_full)

                prompt = (
                    "\n".join(block) + "\n\n"
                    "INDICATORS_PRESET_12:\n" + ta_block_raw + "\n\n"
                    "Decide if there is a trade now. Return STRICT JSON only (no prose) with keys exactly:\n"
                    '{"direction":"LONG|SHORT|NEUTRAL","entry":number,"stop":number,"tp":number,'
                    '"confidence":0..1,"holding_time_hours":number,"holding_time":"string","rationale":"2-3 sentences"}.'
                )

                raw_resp = chat_completion(
                    endpoint=CFG["or_base"],
                    api_key=route.api_key,
                    model=route.model,
                    messages=[{"role":"system","content":AI_SYSTEM},{"role":"user","content":prompt}],
                    timeout=CFG["or_timeout"]
                )
                plan = _parse_ai_json(raw_resp)

                direction = (plan.get("direction") or "").upper()
                entry = _safe_float(plan.get("entry"))
                stop  = _safe_float(plan.get("stop"))
                tp    = _safe_float(plan.get("tp"))
                conf  = _safe_float(plan.get("confidence")) or 0.0

                rr_num = _compute_rr_num(
                    direction,
                    entry if entry is not None else math.nan,
                    stop  if stop  is not None else math.nan,
                    tp    if tp    is not None else math.nan
                )
                rr_text = f"{rr_num:.2f}" if rr_num is not None else "-"

                # RR-фільтр користувача
                try:
                    rr_min = float(us.get("rr_threshold", CFG.get("rr_threshold", 1.5)))
                    if rr_num is not None and rr_num < rr_min:
                        await _send(update, context, f"⚠️ {symbol} скіп (RR < {rr_min}).")
                        indi_md = format_ta_report(symbol, user_tf, CFG["analyze_limit"])
                        await _send(update, context, "📈 Indicators (preset):\n" + indi_md, parse_mode="Markdown")
                        continue
                except Exception:
                    pass

                # зберігаємо OPEN сигнал — ВАЖЛИВО: tf=user_tf
                rr_val = None
                try:
                    rr_val = float(rr_text) if rr_text not in (None, "-", "") else None
                except Exception:
                    rr_val = None

                save_signal_open(
                    user_id=uid or 0,
                    source="analyze_all",
                    symbol=symbol,
                    tf=user_tf,
                    direction=direction or "NEUTRAL",
                    entry=entry,
                    stop=stop,
                    tp=tp,
                    rr=rr_val,
                    analysis_id=analysis_id,
                    snapshot_ts=snapshot_ts,
                    size_usd=size_usd,
                    details={
                        "model": route.model,
                        "ta_markdown": ta_block_full,
                        "plan_raw": plan,
                        "generated_at": snapshot_ts,
                    }
                )

                # Відповідь користувачу
                tz = ZoneInfo(CFG["tz"])
                now_local = datetime.now(tz)
                hold_h = float(plan.get("holding_time_hours", 0.0) or 0.0)
                hold_until_local = now_local + timedelta(hours=hold_h) if hold_h > 0 else None
                hold_line = (
                    f"Recommended hold: {int(round(hold_h))} h"
                    + (f" (до {hold_until_local.strftime('%Y-%m-%d %H:%M %Z')} / {CFG['tz']})" if hold_until_local else "")
                )
                stamp_line = f"Generated: {now_local.strftime('%Y-%m-%d %H:%M %Z')}"

                reply = (
                    f"🤖 AI Trade Plan for {symbol} (TF={user_tf})\n"
                    f"Model: {_current_ai_model()}\n"
                    f"{stamp_line}\n\n"
                    f"Direction: {direction or '-'}\n"
                    f"Confidence: {conf:.2%}\n"
                    f"RR: {rr_text}\n"
                    f"Entry: { _fmt_or_dash(entry) }\n"
                    f"Stop:  { _fmt_or_dash(stop) }\n"
                    f"Take:  { _fmt_or_dash(tp) }\n"
                    f"{hold_line}\n\n"
                    f"Reasoning:\n{plan.get('rationale','—')}\n"
                )
                await _send(update, context, reply)

                indi_md = format_ta_report(symbol, user_tf, CFG["analyze_limit"])
                await _send(update, context, "📈 Indicators (preset):\n" + indi_md, parse_mode="Markdown")

            except Exception as e:
                log.exception("analyze_all %s failed", symbol)
                await _send(update, context, f"⚠️ analyze {symbol} error: {e}")

    except Exception as e:
        log.exception("on_cb_analyze_all failed")
        await _send(update, context, f"⚠️ analyze all error: {e}")

async def on_cb_an_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        await _send(
            update, context,
            "📊 Обери монету з моніторингу або натисни *Analyze ALL*:",
            parse_mode="Markdown",
            reply_markup=symbols_keyboard()
        )
    except Exception as e:
        log.exception("on_cb_an_refresh failed")
        await _send(update, context, f"⚠️ analyze refresh error: {e}")

async def on_cb_goto_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    try:
        uid = q.from_user.id
        ensure_user_row(uid)
        kb = panel_keyboard(uid)
        await _send(update, context, "Панель налаштувань:", reply_markup=kb)
    except Exception as e:
        log.exception("goto_panel failed")
        await _send(update, context, f"⚠️ panel open error: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# AI planner
# ──────────────────────────────────────────────────────────────────────────────
AI_SYSTEM = (
    "You are a concise crypto trading assistant. "
    "Return STRICT JSON only (no prose) with keys exactly: "
    '{"direction":"LONG|SHORT|NEUTRAL","entry":number,"stop":number,"tp":number,'
    '"confidence":0..1,"holding_time_hours":number,"holding_time":"string",'
    '"rationale":"2-3 sentences"} '
    "Use trend/momentum/volatility/strength/volume/pivots data. "
    "Prefer ~1:3 risk-reward when reasonable."
)

# ⬇️ ДОДАЙ поруч із іншими хелперами
def _get_user_model_key(update: Update) -> str:
    try:
        uid = update.effective_user.id if update.effective_user else None
        if uid is None:
            return "auto"
        us = get_user_settings(uid) or {}
        key = (us.get("model_key") or "auto").strip()
        return key or "auto"
    except Exception:
        return "auto"


# ───────────────────────────────
# /ai — оновлений безпечний обробник
# ───────────────────────────────
async def cmd_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /ai [SYMBOL] [TF] — показує TA-звіт і (за наявності роута) план від LLM.
    - Використовує модель з /panel (user_settings.model_key)
    - Виправлено дублювання відповідей
    - Дає зрозумілий reason при відмові LLM
    """
    chat_id = update.effective_chat.id

    # 1) symbol/timeframe з аргументів або дефолти (TF беремо з user_settings)
    args = context.args or []
    symbol = (args[0].upper() if len(args) >= 1 else _pick_default_symbol())
    try:
        uid = update.effective_user.id if update.effective_user else None
        us = get_user_settings(uid) if uid else {}
        user_tf_default = (us.get("timeframe") or CFG.get("analyze_timeframe") or "15m").strip()
    except Exception:
        user_tf_default = CFG.get("analyze_timeframe") or "15m"
    timeframe = (args[1] if len(args) >= 2 else user_tf_default)

    # Прев’ю
    await _safe_send(context.bot, chat_id, f"⏳ Запускаю LLM-аналіз для {symbol} (TF={timeframe})…", parse_mode=None)

    # 2) TA-частина (стабільно як у тебе)
    try:
        ta_text = format_ta_report(symbol, timeframe)
    except TypeError:
        indicators_obj = None
        try:
            from services.analyzer_core import compute_indicators  # type: ignore
            indicators_obj = compute_indicators(symbol, timeframe)
        except Exception:
            pass
        ta_text = format_ta_report(symbol, timeframe, indicators_obj)

    await _safe_send(context.bot, chat_id, ta_text, parse_mode="Markdown")

    # 3) LLM-частина — тільки якщо дозволено guard-ом
    plan_text: str | None = None
    if _llm_allowed():
        try:
            # ▸ маршрут з урахуванням вибраної в /panel моделі
            user_model_key = _get_user_model_key(update)
            route = pick_route(symbol, user_model_key=user_model_key)
            if not route:
                raise RuntimeError("no LLM route configured (перевір OR_SLOTS/OPENROUTER_KEYS або LOCAL_LLM_*)")

            # ▸ стислий промпт + безпечні параметри
            def _strip_md_local(s: str) -> str:
                s = re.sub(r"[*_`]", "", s or "")
                s = re.sub(r"[^\S\r\n]+", " ", s).strip()
                return s

            ta_block_raw = _strip_md_local(ta_text)
            llm_prompt = (
                f"Symbol: {symbol}\nTimeframe: {timeframe}\n"
                f"Indicators (preset-12):\n{ta_block_raw}\n\n"
                "Return STRICT JSON only (no prose) with keys exactly:\n"
                '{"direction":"LONG|SHORT|NEUTRAL","entry":number,"stop":number,"tp":number,'
                '"confidence":0..1,"holding_time_hours":number,"holding_time":"string","rationale":"2-3 sentences"}'
            )

            endpoint = getattr(route, "base", None) or CFG.get("or_base")
            model = getattr(route, "model", None) or CFG.get("llm_model", "openai/gpt-4o-mini")
            api_key = getattr(route, "api_key", None)
            timeout = int(getattr(route, "timeout", None) or CFG.get("or_timeout", 30))

            # Підтримка sync/async utils.openrouter.chat_completion
            kwargs = dict(
                endpoint=endpoint,
                api_key=api_key,
                model=model,
                messages=[
                    {"role": "system", "content": AI_SYSTEM},
                    {"role": "user", "content": llm_prompt},
                ],
                temperature=0.2,
                timeout=timeout,
            )
            resp = await chat_completion(**kwargs) if inspect.iscoroutinefunction(chat_completion) else chat_completion(**kwargs)

            # ▸ витягнути текст незалежно від форми відповіді
            raw = _extract_llm_text(resp)
            if not raw:
                raise RuntimeError("empty LLM content")

            # ▸ НЕ шлемо сирий JSON — коротка красивa відповідь
            plan_text = raw

        except Exception as e:
            log.warning("LLM call failed: %s", e)
            plan_text = None
    else:
        log.info("LLM disabled by guard; skipping /ai generation")

    # 4) Відправити план або дружній фолбек (ОДИН раз)
    if plan_text and plan_text.strip():
        await _safe_send(context.bot, chat_id, plan_text, parse_mode="Markdown")
    else:
        fb = (
            f"⚠️ *{symbol}* *(TF={timeframe})* — **План від LLM недоступний**\n\n"
            "🔍 *Можливі причини:*\n"
            "• LLM вимкнено в налаштуваннях\n"
            "• API ключі OpenRouter не задані або недійсні\n"
            "• Модель не обрана в панелі\n"
            "• Сервер LLM тимчасово недоступний\n\n"
            "🛠 *Як виправити:*\n"
            "1️⃣ Відкрий `/panel` → *Model* → обери модель\n"
            "2️⃣ Перевір `.env` файл:\n"
            "   • `LLM_DISABLED=0`\n"
            "   • `OPENROUTER_KEYS=your_openrouter_key`\n"
            "   • `OR_SLOTS=deepseek/deepseek-chat`\n"
            "3️⃣ Або налаштуй локальний LLM:\n"
            "   • `LOCAL_LLM_URL=http://localhost:1234/v1`\n\n"
            "💡 Без LLM бот працює в режимі *індикаторів* — сигнали формуються на основі технічного аналізу (Gate Logic)."
        )
        await _safe_send(context.bot, chat_id, fb, parse_mode="Markdown")

# ──────────────────────────────────────────────────────────────────────────────
async def on_cb_indicators(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = (q.data or "")
    if not data.startswith("indic:"): return
    sym = data.split(":",1)[1].upper()
    try:
        uid = q.from_user.id if q.from_user else None
        us = get_user_settings(uid) if uid else {}
        user_tf = (us.get("timeframe") or CFG["analyze_timeframe"]).strip()
        indi = format_ta_report(sym, user_tf, CFG["analyze_limit"])
        
        # Додаємо Long/Short Ratio
        try:
            from market_data.long_short_ratio import get_sentiment_short
            sentiment = await get_sentiment_short(sym, period="5m")
            if sentiment:
                ls_emoji = sentiment.get('bias_emoji', '⚖️')
                long_pct = sentiment.get('long_pct', 0)
                short_pct = sentiment.get('short_pct', 0)
                ls_ratio = sentiment.get('ls_ratio', 1)
                indi += f"\n- 📊 L/S Ratio: {ls_emoji} Long {long_pct:.0f}% / Short {short_pct:.0f}% (ratio {ls_ratio:.2f})"
                indi += f"\n  ↳ >1.5 багато лонгів (squeeze?), <0.7 багато шортів"
        except Exception as e:
            log.debug("L/S Ratio fetch failed: %s", e)
        
        await _send(update, context, f"📈 Indicators (preset):\n{indi}", parse_mode="Markdown")
    except Exception as e:
        log.exception("on_cb_indicators failed")
        await _send(update, context, f"⚠️ indicators error: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# BTC/ETH dependency (callback)
# ──────────────────────────────────────────────────────────────────────────────
async def on_cb_dep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = (q.data or "")
    if not data.startswith("dep:"): return
    sym = data.split(":",1)[1].upper()
    await _send(update, context, f"⏳ Рахую залежність BTC/ETH для {sym}…")
    try:
        report = await _dependency_report(sym, CFG["analyze_timeframe"], limit=300)
        await _send(update, context, report, parse_mode="Markdown")
    except Exception as e:
        log.exception("dep failed")
        await _send(update, context, f"⚠️ dep error: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# autopost_now (ручний запуск фонового скану)
# ──────────────────────────────────────────────────────────────────────────────
async def autopost_now(update, context):
    chat_id = update.effective_chat.id
    try:
        await context.bot.send_message(chat_id, "⏳ Запускаю автопост…")

        # Працюємо і з async, і з sync реалізацією run_autopост_once
        if inspect.iscoroutinefunction(run_autopost_once):
            msgs = await run_autopост_once(context.application)
        else:
            msgs = await asyncio.to_thread(run_autopост_once, context.application)

        sent = 0
        for m in msgs or []:
            try:
                await context.bot.send_message(
                    m.get("chat_id", chat_id),
                    m.get("text",""),
                    parse_mode=m.get("parse_mode"),
                    disable_web_page_preview=m.get("disable_web_page_preview", True),
                )
                sent += 1
            except Exception as e:
                logging.getLogger("autopost").warning("send fail: %s", e)
        await context.bot.send_message(chat_id, f"✅ Готово: автопост надіслав {sent} повідомлень.")
    except Exception as e:
        await context.bot.send_message(chat_id, f"⚠️ autopost_now error: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# CALLBACK: ai:<SYM>  (плитка "🤖 AI <SYM>")
# ──────────────────────────────────────────────────────────────────────────────
async def on_cb_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє кнопку ai:<SYM> з /top або /analyze меню та делегує в /ai."""
    try:
        q = update.callback_query
        await q.answer()
        data = (q.data or "")
        if not data.startswith("ai:"):
            return
        symbol = data.split(":", 1)[1].strip().upper()
        # делегуємо в новий cmd_ai: підставимо args так, ніби це /ai <SYM>
        context.args = [symbol]
        await cmd_ai(update, context)
    except Exception as e:
        log.exception("on_cb_ai failed")
        await _send(update, context, f"⚠️ callback error: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# register (delegated to handlers.register)
# ──────────────────────────────────────────────────────────────────────────────
def register_handlers(app: Application):
    """Register all handlers - delegates to handlers.register module."""
    from telegram_bot.handlers.register import register_handlers as _register
    _register(app)

# ──────────────────────────────────────────────────────────────────────────────
# daily / winrate
# ──────────────────────────────────────────────────────────────────────────────
async def daily_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    try:
        metrics, md = compute_daily_summary(uid)
        await _send(update, context, md, parse_mode="Markdown")
    except Exception as e:
        await _send(update, context, f"⚠️ daily_now error: {e}")

async def winrate_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    days = 7
    if context.args:
        try:
            days = max(1, int(context.args[0]))
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to convert value: {e}")
            pass
    try:
        with _conn_local() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COALESCE(rr_threshold,1.5) FROM user_settings WHERE user_id=?", (uid,))
            row = cur.fetchone()
            rr_min = float(row[0] if row else 1.5)
            t1 = int(time.time()); t0 = t1 - days*86400
            cur.execute("""
                SELECT status, rr, pnl_pct FROM signals
                WHERE user_id=? AND status IN ('WIN','LOSS')
                  AND COALESCE(rr,0) >= ? AND COALESCE(ts_closed, ts_created) BETWEEN ? AND ?
            """, (uid, rr_min, t0, t1))
            rows = cur.fetchall()
        wins = sum(1 for r in rows if r["status"]=="WIN")
        n = len(rows)
        winrate = (wins/n*100.0) if n else 0.0
        avg_rr = (sum(float(r["rr"]) for r in rows)/n) if n else 0.0
        avg_pnl = (sum(float(r["pnl_pct"] or 0.0) for r in rows)/n) if n else 0.0
        md = (
            f"**📈 Winrate {days}d (RR≥{rr_min:g})**\n\n"
            f"Trades: **{n}** | WIN: **{wins}** | LOSS: **{n-wins}** | Winrate: **{winrate:.2f}%**\n"
            f"Avg RR: **{avg_rr:.2f}** | Avg PnL: **{avg_pnl:.2f}%**"
        )
        await _send(update, context, md, parse_mode="Markdown")
    except Exception as e:
        await _send(update, context, f"⚠️ winrate_now error: {e}")
