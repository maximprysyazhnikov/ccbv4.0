# telegram_bot/panel.py
from __future__ import annotations
from typing import List
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram_bot import panel_neutral
from core_config import CFG
from utils.user_settings import get_user_settings, set_user_settings, ensure_user_row

TF_OPTIONS: List[str] = ["5m", "15m", "1h", "4h", "1d"]
AP_RR_OPTIONS: List[float] = [1.0, 1.5, 2.0, 3.0]
QUALITY_GATE_OPTIONS: List[int] = [50, 60, 70, 80]
LOCALE_OPTIONS: List[str] = ["uk", "en"]
SCALP_SL_OPTIONS: List[float] = [0.2, 0.3, 0.5, 0.7]
SCALP_TP_OPTIONS: List[float] = [0.5, 0.9, 1.2, 1.5]
SLIPPAGE_OPTIONS: List[float] = [0.02, 0.05, 0.08, 0.1]

def _bool_emoji(v: int | bool | None) -> str:
    return "✅ ON" if bool(v or 0) else "❌ OFF"

def _mark(value: str, current: str) -> str:
    return f"✅ {value}" if str(value) == str(current) else value

def _current_symbols(user_id: int) -> tuple[list[str], bool]:
    us = get_user_settings(user_id) or {}
    custom_symbols = us.get("monitored_symbols") or ""
    if custom_symbols:
        return [s.strip() for s in custom_symbols.split(",") if s.strip()], False
    return [s.strip() for s in CFG.get("symbols", []) if s.strip()], True

def symbols_overview_text(user_id: int) -> str:
    symbols, is_default = _current_symbols(user_id)
    source = "default .env" if is_default else "user settings"
    if not symbols:
        return "📋 *Monitored Symbols*\n\nNo symbols configured."

    lines = [f"📋 *Monitored Symbols* ({len(symbols)})", f"Source: `{source}`", ""]
    for i in range(0, len(symbols), 4):
        lines.append("`" + "`, `".join(symbols[i:i + 4]) + "`")
    return "\n".join(lines)

def _model_options() -> List[str]:
    models: List[str] = []
    try:
        for s in (CFG.get("or_slots") or []):
            m = s.get("model")
            if m and m not in models:
                models.append(m)
    except Exception:
        pass
    if not models:
        models = [CFG.get("or_model") or "deepseek/deepseek-chat"]
    return ["auto"] + models

def panel_keyboard(user_id: int) -> InlineKeyboardMarkup:
    ensure_user_row(user_id)
    us = get_user_settings(user_id) or {}

    timeframe = us.get("timeframe") or CFG.get("analyze_timeframe", "15m")
    autopost  = int(us.get("autopost") or 0)
    ap_tf     = us.get("autopost_tf") or timeframe
    ap_rr     = float(us.get("autopost_rr") or 1.5)
    model_key = (us.get("model_key") or "auto")
    locale    = (us.get("locale") or CFG.get("default_locale","uk")).lower()

    daily_tracker   = int(us.get("daily_tracker") or 0)
    winrate_tracker = int(us.get("winrate_tracker") or 0)
    
    # Scalping settings
    scalping_mode = int(us.get("scalping_mode") or 0)
    scalping_sl   = float(us.get("scalping_sl_pct") or 0.3)
    scalping_tp   = float(us.get("scalping_tp_pct") or 0.9)
    slippage      = float(us.get("slippage_pct") or 0.05)
    quality_gate  = int(us.get("quality_gate_pct") or 70)

    rows: list[list[InlineKeyboardButton]] = []

    # Autopost ON/OFF
    rows.append([
        InlineKeyboardButton(
            f"Autopost: {_bool_emoji(autopost)}",
            callback_data=f"panel:toggle_autopost:{1 if not autopost else 0}"
        )
    ])

    rows.append([
        InlineKeyboardButton("⚙️ Neutral", callback_data="panel:neutral"),
        InlineKeyboardButton("📊 KPI", callback_data="panel:kpi"),
        InlineKeyboardButton("� Risk Monitor", callback_data="panel:risk_monitor"),
        InlineKeyboardButton("�📦 Orders", callback_data="orders:refresh"),
    ])
    
    # ───────── SCALPING SECTION ─────────
    scalp_emoji = "⚡" if scalping_mode else "🔇"
    rows.append([
        InlineKeyboardButton(
            f"{scalp_emoji} Scalping: {_bool_emoji(scalping_mode)}",
            callback_data=f"panel:toggle_scalping:{1 if not scalping_mode else 0}"
        )
    ])
    
    # Scalping SL %
    rows.append([
        InlineKeyboardButton(
            _mark(f"SL {sl:.1f}%", f"SL {scalping_sl:.1f}%"),
            callback_data=f"panel:set_scalp_sl:{sl}"
        ) for sl in SCALP_SL_OPTIONS
    ])
    
    # Scalping TP %
    rows.append([
        InlineKeyboardButton(
            _mark(f"TP {tp:.1f}%", f"TP {scalping_tp:.1f}%"),
            callback_data=f"panel:set_scalp_tp:{tp}"
        ) for tp in SCALP_TP_OPTIONS
    ])
    
    # Slippage %
    rows.append([
        InlineKeyboardButton(
            _mark(f"Slip {sp:.2f}%", f"Slip {slippage:.2f}%"),
            callback_data=f"panel:set_slippage:{sp}"
        ) for sp in SLIPPAGE_OPTIONS
    ])
    
    # ───────── MONITORED SYMBOLS ─────────
    symbols_list, is_default_symbols = _current_symbols(user_id)
    if symbols_list:
        from utils.symbol_validator import format_symbols_for_display
        symbols_display = format_symbols_for_display(symbols_list, max_display=8)
        if is_default_symbols:
            symbols_display = f"{symbols_display} (default)"
    else:
        symbols_display = "none"
    
    rows.append([
        InlineKeyboardButton(
            f"📋 Symbols: {symbols_display}",
            callback_data="panel:show_symbols:"
        )
    ])
    rows.append([
        InlineKeyboardButton(
            "✏️ Edit Symbols",
            callback_data="panel:edit_symbols:"
        )
    ])

    rows.append([
        InlineKeyboardButton("🥇 /metals", callback_data="panel:show_metals:"),
        InlineKeyboardButton("⚡ Metals Scalp", callback_data="panel:metals_scalp:"),
    ])
    rows.append([
        InlineKeyboardButton("📈 Metals KPI 1д", callback_data="metals_kpi:1"),
        InlineKeyboardButton("7д", callback_data="metals_kpi:7"),
        InlineKeyboardButton("30д", callback_data="metals_kpi:30"),
    ])

    # ───────── TIMEFRAME (for /ai, /req) ─────────
    rows.append([
        InlineKeyboardButton("🕐 TF:", callback_data="panel:noop"),
        *[InlineKeyboardButton(_mark(tf, timeframe), callback_data=f"panel:set_tf:{tf}") for tf in TF_OPTIONS]
    ])

    # ───────── AUTOPOST TF ─────────
    rows.append([
        InlineKeyboardButton("📡 AP TF:", callback_data="panel:noop"),
        *[InlineKeyboardButton(_mark(tf, ap_tf), callback_data=f"panel:set_ap_tf:{tf}") for tf in TF_OPTIONS]
    ])

    # Autopost RR threshold
    rows.append([
        InlineKeyboardButton(
            _mark(f"AP RR {r:.1f}", f"AP RR {ap_rr:.1f}"),
            callback_data=f"panel:set_ap_rr:{r}"
        ) for r in AP_RR_OPTIONS
    ])
    
    # Quality Gate threshold
    rows.append([
        InlineKeyboardButton(
            _mark(f"QG {qg}%", f"QG {quality_gate}%"),
            callback_data=f"panel:set_quality_gate:{qg}"
        ) for qg in QUALITY_GATE_OPTIONS
    ])

    # Models (групами по 4)
    m_row: list[InlineKeyboardButton] = []
    for m in _model_options():
        cap = m if m != model_key else f"✅ {m}"
        m_row.append(InlineKeyboardButton(cap, callback_data=f"panel:set_model:{m}"))
        if len(m_row) == 4:
            rows.append(m_row); m_row = []
    if m_row: rows.append(m_row)

    # Locale
    rows.append([
        InlineKeyboardButton(loc.upper() if loc != locale else f"✅ {loc.upper()}",
                             callback_data=f"panel:set_locale:{loc}")
        for loc in LOCALE_OPTIONS
    ])

    # Daily / Winrate (вказуємо цільове значення прямо у callback_data)
    rows.append([
        InlineKeyboardButton(
            f"Daily: {_bool_emoji(daily_tracker)}",
            callback_data=f"panel:toggle_daily:{0 if daily_tracker else 1}"
        ),
        InlineKeyboardButton(
            f"Winrate: {_bool_emoji(winrate_tracker)}",
            callback_data=f"panel:toggle_winrate:{0 if winrate_tracker else 1}"
        ),
    ])

    rows.append([InlineKeyboardButton("ℹ️ Help", callback_data="panel:help:")])
    return InlineKeyboardMarkup(rows)

def apply_panel_action(user_id: int, action: str, value: str) -> None:
    ensure_user_row(user_id)

    if action == "toggle_autopost":
        try: v = int(value)
        except (ValueError, TypeError, ConnectionError) as e: v = 0
        set_user_settings(user_id, autopost=v)

    elif action == "set_tf" and value:
        set_user_settings(user_id, timeframe=value)

    elif action == "set_ap_tf" and value:
        set_user_settings(user_id, autopost_tf=value)

    elif action == "set_ap_rr":
        try: rr = float(value)
        except (ValueError, TypeError, ConnectionError) as e: rr = 1.5
        set_user_settings(user_id, autopost_rr=rr)

    elif action == "set_model":
        mk = (value or "auto").strip()
        if mk: set_user_settings(user_id, model_key=mk)

    elif action == "set_locale":
        loc = (value or "").strip().lower()
        if loc in LOCALE_OPTIONS: set_user_settings(user_id, locale=loc)

    elif action == "toggle_daily":
        try: newv = int(value)
        except (ValueError, TypeError, ConnectionError) as e: newv = 0 if (get_user_settings(user_id).get("daily_tracker") or 0) else 1
        set_user_settings(user_id, daily_tracker=newv)

    elif action == "toggle_winrate":
        try: newv = int(value)
        except (ValueError, TypeError, ConnectionError) as e: newv = 0 if (get_user_settings(user_id).get("winrate_tracker") or 0) else 1
        set_user_settings(user_id, winrate_tracker=newv)

    # ── Scalping actions ──
    elif action == "toggle_scalping":
        try: v = int(value)
        except (ValueError, TypeError, ConnectionError) as e: v = 0
        set_user_settings(user_id, scalping_mode=v)
    
    elif action == "set_scalp_sl":
        try: sl = float(value)
        except (ValueError, TypeError, ConnectionError) as e: sl = 0.3
        set_user_settings(user_id, scalping_sl_pct=sl)
    
    elif action == "set_scalp_tp":
        try: tp = float(value)
        except (ValueError, TypeError, ConnectionError) as e: tp = 0.9
        set_user_settings(user_id, scalping_tp_pct=tp)
    
    elif action == "set_slippage":
        try: slip = float(value)
        except (ValueError, TypeError, ConnectionError) as e: slip = 0.05
        set_user_settings(user_id, slippage_pct=slip)
    
    elif action == "set_quality_gate":
        try: qg = int(value)
        except (ValueError, TypeError, ConnectionError) as e: qg = 50
        set_user_settings(user_id, quality_gate_pct=qg)

    # ── Symbols actions ──
    elif action == "edit_symbols":
        # This triggers conversation handler - no direct action here
        pass

    elif action == "show_symbols":
        pass

    elif action == "show_metals":
        pass

    elif action == "metals_scalp":
        pass

    elif action == "metals_kpi":
        pass

    elif action == "set_symbols":
        # Validate and save symbols
        from utils.symbol_validator import validate_symbols
        valid, invalid = validate_symbols(value)
        if valid:
            set_user_settings(user_id, monitored_symbols=",".join(valid))
        return {"valid": valid, "invalid": invalid}
    
    elif action == "reset_symbols":
        set_user_settings(user_id, monitored_symbols="")

    elif action == "help":
        pass
