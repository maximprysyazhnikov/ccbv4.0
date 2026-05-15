"""AI command handlers (/ai, /req, /analyze)."""
from __future__ import annotations

import asyncio
import math
import logging
import json
import re
import os
import time
import uuid
import inspect
from typing import List
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from core_config import CFG
from router.analyzer_router import pick_route
from utils.openrouter import chat_completion
from utils.ta_formatter import format_ta_report
from market_data.candles import get_ohlcv
from utils.user_settings import get_user_settings
from telegram_bot.panel import panel_keyboard
from utils.user_settings import ensure_user_row
from telegram_bot.handlers.helpers import (
    _send, _looks_like_symbol, _pick_default_symbol,
    _safe_float, _fmt_or_dash, _llm_allowed, _extract_llm_text, _current_ai_model
)

log = logging.getLogger("tg.handlers")

AI_SYSTEM = (
    "You are an expert cryptocurrency trading analyst. Analyze indicators and provide detailed trading plan.\n\n"
    "ANALYSIS RULES:\n"
    "1. TREND: EMA50 vs EMA200 (golden/death cross), SMA7/25 for short-term\n"
    "2. MOMENTUM: RSI (oversold<30, overbought>70), StochRSI, MACD histogram direction\n"
    "3. VOLATILITY: ATR% for stop placement, Bollinger %B for squeeze/breakout\n"
    "4. VOLUME: OBV trend, MFI divergence, Volume vs 20-bar average\n"
    "5. LEVELS: Pivot points (S1-S3, R1-R3), VWAP as dynamic support/resistance\n"
    "6. Risk-reward minimum 1:2, prefer 1:2.5-1:3.5\n"
    "7. If conflicting signals or low confidence, return NEUTRAL with analysis\n\n"
    "OUTPUT FORMAT (JSON only):\n"
    "{\n"
    '  "direction": "LONG|SHORT|NEUTRAL",\n'
    '  "entry": price_number,\n'
    '  "stop": stop_loss_price,\n'
    '  "tp": take_profit_price,\n'
    '  "tp2": optional_second_target,\n'
    '  "confidence": 0.0-1.0,\n'
    '  "holding_time": "4h|12h|1d|3d",\n'
    '  "key_levels": {"support": price, "resistance": price},\n'
    '  "signals_bullish": ["list", "of", "bullish", "signals"],\n'
    '  "signals_bearish": ["list", "of", "bearish", "signals"],\n'
    '  "rationale": "Detailed 3-5 sentence analysis explaining entry logic, key levels, and risk"\n'
    "}"
)


def _get_user_model_key(update: Update) -> str:
    """Get user's model key from settings."""
    try:
        uid = update.effective_user.id if update.effective_user else None
        if uid is None:
            return "auto"
        us = get_user_settings(uid) or {}
        key = (us.get("model_key") or "auto").strip()
        return key or "auto"
    except Exception:
        return "auto"


def _parse_ai_json(txt: str) -> dict:
    """Parse AI JSON response."""
    try:
        t = (txt or "").strip()
        if t.startswith("```"):
            t = t.strip("` \n")
            t = re.sub(r"^json\s*", "", t, flags=re.I)
            t = re.sub(r"\s*json$", "", t, flags=re.I)
            t = t.strip("` \n")
        data = json.loads(t)
        return {
            "direction": str(data.get("direction", "")).upper(),
            "entry": float(data.get("entry", "nan")),
            "stop": float(data.get("stop", "nan")),
            "tp": float(data.get("tp", "nan")),
            "confidence": float(data.get("confidence", 0.0)),
            "holding_time_hours": float(data.get("holding_time_hours", 0.0)),
            "holding_time": str(data.get("holding_time", "")).strip(),
            "rationale": str(data.get("rationale", "")).strip(),
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
            "tp": num(r"(?:take(?:-|\s*)profit|tp)"),
            "confidence": 0.5,
            "holding_time_hours": 0.0,
            "holding_time": "",
            "rationale": (txt or "").strip()
        }


def _compute_rr_num(direction: str, entry: float, stop: float, tp: float):
    """Compute RR number."""
    try:
        if any(math.isnan(x) for x in [entry, stop, tp]):
            return None
        if direction == "LONG":
            risk = entry - stop
            reward = tp - entry
        elif direction == "SHORT":
            risk = stop - entry
            reward = entry - tp
        else:
            return None
        if risk <= 0 or reward <= 0:
            return None
        return float(reward / risk)
    except Exception:
        return None


def _pct(series: List[float]) -> List[float]:
    """Calculate percentage changes."""
    out = []
    for i in range(1, len(series)):
        prev = series[i-1] or 0.0
        out.append(0.0 if prev == 0 else (series[i]-series[i-1]) / prev)
    return out


def _corr(a: List[float], b: List[float]) -> float:
    """Calculate correlation."""
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
    """Calculate beta."""
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
    """Format number."""
    try:
        v = float(x)
        if v != v:  # NaN
            return dash
        return f"{v:.{d}f}"
    except Exception:
        return dash


async def _dependency_report(symbol: str, timeframe: str, limit: int = 300) -> str:
    """Calculate dependency report."""
    t_data = get_ohlcv(symbol, timeframe, limit)
    b_data = get_ohlcv("BTCUSDT", timeframe, limit)
    e_data = get_ohlcv("ETHUSDT", timeframe, limit)
    if not t_data or not b_data or not e_data:
        return "_No data to compute dependency_"
    
    t_close = [x["close"] for x in t_data]
    b_close = [x["close"] for x in b_data]
    e_close = [x["close"] for x in e_data]
    
    t_ret = _pct(t_close)
    b_ret = _pct(b_close)
    e_ret = _pct(e_close)
    
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
    """Handle /req command."""
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


async def cmd_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /ai command."""
    chat_id = update.effective_chat.id
    
    args = context.args or []
    symbol = (args[0].upper() if len(args) >= 1 else _pick_default_symbol())
    try:
        uid = update.effective_user.id if update.effective_user else None
        us = get_user_settings(uid) if uid else {}
        user_tf_default = (us.get("timeframe") or CFG.get("analyze_timeframe") or "15m").strip()
    except Exception:
        user_tf_default = CFG.get("analyze_timeframe") or "15m"
    timeframe = (args[1] if len(args) >= 2 else user_tf_default)
    
    async def _safe_send(bot, chat_id, text: str, parse_mode: str | None = "Markdown"):
        if not text:
            return
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
        except Exception as e:
            log.warning("send_message failed: %s", e)
    
    await _safe_send(context.bot, chat_id, f"⏳ Запускаю LLM-аналіз для {symbol} (TF={timeframe})…", parse_mode=None)
    
    try:
        ta_text = format_ta_report(symbol, timeframe)
    except TypeError:
        indicators_obj = None
        try:
            from services.analyzer_core import compute_indicators
            indicators_obj = compute_indicators(symbol, timeframe)
        except Exception:
            pass
        ta_text = format_ta_report(symbol, timeframe, indicators_obj)
    
    await _safe_send(context.bot, chat_id, ta_text, parse_mode=None)
    
    plan_text: str | None = None
    if _llm_allowed():
        try:
            user_model_key = _get_user_model_key(update)
            route = pick_route(symbol, user_model_key=user_model_key)
            if not route:
                raise RuntimeError("no LLM route configured")
            
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
            
            raw = _extract_llm_text(resp)
            if not raw:
                raise RuntimeError("empty LLM content")
            
            plan_text = raw
        
        except Exception as e:
            log.warning("LLM call failed: %s", e)
            plan_text = None
    else:
        log.info("LLM disabled by guard; skipping /ai generation")
    
    if plan_text and plan_text.strip():
        # Try to format JSON nicely
        try:
            import json
            # Clean up JSON if wrapped in markdown code block
            clean_text = plan_text.strip()
            if clean_text.startswith("```"):
                clean_text = clean_text.strip("`").strip()
                if clean_text.lower().startswith("json"):
                    clean_text = clean_text[4:].strip()
            
            data = json.loads(clean_text)
            
            # Direction emoji and styling
            direction = data.get('direction', 'N/A').upper()
            if direction == 'LONG':
                dir_emoji = '🟢'
                dir_style = '**LONG** ↑'
            elif direction == 'SHORT':
                dir_emoji = '🔴'
                dir_style = '**SHORT** ↓'
            else:
                dir_emoji = '⚖️'
                dir_style = '**NEUTRAL** ↔'
            
            # Parse values
            entry = data.get('entry')
            stop = data.get('stop')
            tp = data.get('tp')
            confidence = data.get('confidence', 0)
            holding = data.get('holding_time', 'N/A')
            rationale = data.get('rationale', 'N/A')
            
            # Check if values are valid (not 0 or None)
            has_valid_levels = entry and stop and tp and entry > 0 and stop > 0 and tp > 0
            
            # Calculate RR
            rr = None
            if has_valid_levels:
                try:
                    risk = abs(entry - stop)
                    reward = abs(tp - entry)
                    if risk > 0:
                        rr = reward / risk
                except (ValueError, TypeError) as e:
                    logger.warning(f"Failed to convert value: {e}")
                    pass
            conf_pct = int(float(confidence) * 100) if confidence else 0
            conf_filled = int(conf_pct / 10)
            conf_bar = '█' * conf_filled + '░' * (10 - conf_filled)
            
            # Format prices
            def fmt_price(p):
                if p is None or p == 0:
                    return 'N/A'
                if p >= 1000:
                    return f"${p:,.2f}"
                elif p >= 1:
                    return f"${p:.4f}"
                else:
                    return f"${p:.6f}"
            
            # Extract additional fields
            tp2 = data.get('tp2')
            key_levels = data.get('key_levels', {})
            signals_bull = data.get('signals_bullish', [])
            signals_bear = data.get('signals_bearish', [])
            
            # Build message
            formatted = []
            formatted.append(f"🤖 *AI Trading Plan* — {symbol}")
            formatted.append(f"━━━━━━━━━━━━━━━━━━━━")
            formatted.append("")
            formatted.append(f"{dir_emoji} *Напрямок:* {dir_style}")
            formatted.append("")
            
            # Show levels only if valid (not NEUTRAL with zeros)
            if has_valid_levels:
                formatted.append(f"📍 *Торгові рівні:*")
                formatted.append(f"   ▸ Entry: `{fmt_price(entry)}`")
                formatted.append(f"   ▸ Stop Loss: `{fmt_price(stop)}`")
                formatted.append(f"   ▸ Take Profit 1: `{fmt_price(tp)}`")
                if tp2:
                    formatted.append(f"   ▸ Take Profit 2: `{fmt_price(tp2)}`")
                if rr:
                    rr_emoji = '✅' if rr >= 2 else ('⚠️' if rr >= 1.5 else '❌')
                    formatted.append(f"   ▸ Risk/Reward: `1:{rr:.2f}` {rr_emoji}")
                formatted.append("")
                
                # Key support/resistance
                if key_levels:
                    sup = key_levels.get('support')
                    res = key_levels.get('resistance')
                    if sup or res:
                        formatted.append(f"🔑 *Ключові рівні:*")
                        if sup:
                            formatted.append(f"   ▸ Support: `{fmt_price(sup)}`")
                        if res:
                            formatted.append(f"   ▸ Resistance: `{fmt_price(res)}`")
                        formatted.append("")
            else:
                formatted.append(f"📍 *Рівні:* _не визначені (сигнали суперечливі)_")
                formatted.append("")
            
            # Bullish/Bearish signals
            if signals_bull or signals_bear:
                if signals_bull:
                    bull_str = ", ".join(signals_bull[:4]) if isinstance(signals_bull, list) else str(signals_bull)
                    formatted.append(f"🟢 *Бичачі:* {bull_str}")
                if signals_bear:
                    bear_str = ", ".join(signals_bear[:4]) if isinstance(signals_bear, list) else str(signals_bear)
                    formatted.append(f"🔴 *Ведмежі:* {bear_str}")
                formatted.append("")
            
            formatted.append(f"📊 *Впевненість:* {conf_bar} {conf_pct}%")
            if holding and holding != "0 hours" and holding != "0h" and holding != "N/A":
                formatted.append(f"⏱ *Час утримання:* {holding}")
            formatted.append("")
            formatted.append(f"💡 *Аналіз:*")
            formatted.append(f"_{rationale}_")
            formatted.append("")
            formatted.append(f"⚠️ _NFA. DYOR._")
            
            plan_text = "\n".join(formatted)
            await _safe_send(context.bot, chat_id, plan_text, parse_mode="Markdown")
            
            # 📊 Save trade to DB if LONG or SHORT with valid levels
            if direction in ('LONG', 'SHORT') and has_valid_levels:
                try:
                    from services.trade_engine import open_trade_from_signal
                    signal_data = {
                        "id": f"ai_{symbol}_{timeframe}_{int(datetime.now().timestamp())}",
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "direction": direction,
                        "entry": entry,
                        "sl": stop,
                        "tp": tp,
                        "rr": rr,
                        "ind": {},  # No indicators from AI
                        "gate_score": None,
                        "gate_total": None,
                    }
                    trade_id = open_trade_from_signal(signal_data, trade_mode="ai")
                    if trade_id:
                        log.info(f"[AI] Trade #{trade_id} opened: {symbol} {direction}")
                        await _safe_send(context.bot, chat_id, f"✅ Трейд #{trade_id} відкрито в `/orders`", parse_mode="Markdown")
                    else:
                        log.info(f"[AI] Trade not opened (already exists or error): {symbol}")
                        try:
                            from services.trade_engine import get_open_trade
                            existing = get_open_trade(symbol, timeframe)
                            if existing:
                                ex_id = existing.get('id')
                                ex_dir = existing.get('direction')
                                ex_tf = existing.get('timeframe')
                                await _safe_send(context.bot, chat_id,
                                    f"⚠️ Трейд не відкрито — вже є відкритий трейд #{ex_id} `{symbol}` `{ex_dir}` (TF={ex_tf}). Перевір `/orders`",
                                    parse_mode="Markdown")
                            else:
                                await _safe_send(context.bot, chat_id, f"⚠️ Трейд не відкрито (помилка або дубль). Перевір логи.", parse_mode="Markdown")
                        except Exception as e:
                            log.warning(f"[AI] Failed to query open trade: {e}")

                except Exception as te:
                    log.warning(f"[AI] Failed to save trade: {te}")
            
        except (json.JSONDecodeError, Exception) as e:
            log.warning("JSON format failed: %s", e)
            # If not JSON or formatting fails, send as plain text
            await _safe_send(context.bot, chat_id, plan_text, parse_mode=None)
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


async def on_cb_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle ai: callback."""
    try:
        q = update.callback_query
        await q.answer()
        data = (q.data or "")
        if not data.startswith("ai:"):
            return
        symbol = data.split(":", 1)[1].strip().upper()
        context.args = [symbol]
        await cmd_ai(update, context)
    except Exception as e:
        log.exception("on_cb_ai failed")
        await _send(update, context, f"⚠️ callback error: {e}")


async def on_cb_indicators(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle indicators callback."""
    q = update.callback_query
    await q.answer()
    data = (q.data or "")
    if not data.startswith("indic:"):
        return
    sym = data.split(":", 1)[1].upper()
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


async def on_cb_dep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle dependency callback."""
    q = update.callback_query
    await q.answer()
    data = (q.data or "")
    if not data.startswith("dep:"):
        return
    sym = data.split(":", 1)[1].upper()
    await _send(update, context, f"⏳ Рахую залежність BTC/ETH для {sym}…")
    try:
        report = await _dependency_report(sym, CFG["analyze_timeframe"], limit=300)
        await _send(update, context, report, parse_mode="Markdown")
    except Exception as e:
        log.exception("dep failed")
        await _send(update, context, f"⚠️ dep error: {e}")


def symbols_keyboard(user_id: int = None):
    """Get symbols keyboard - uses user's monitored_symbols if available."""
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


async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /analyze command."""
    try:
        uid = update.effective_user.id if update.effective_user else None
        await _send(
            update, context,
            "📊 Обери монету з моніторингу або натисни *Analyze ALL*:",
            parse_mode="Markdown",
            reply_markup=symbols_keyboard(uid)
        )
    except Exception as e:
        log.exception("/analyze failed")
        await _send(update, context, f"⚠️ analyze error: {e}")


async def on_cb_analyze_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle analyze all callback."""
    q = update.callback_query
    await q.answer()
    try:
        # Import save_signal_open from parent handlers module
        import telegram_bot.handlers as handlers_module
        save_signal_open = handlers_module.save_signal_open
        uid = q.from_user.id if q.from_user else None
        us = get_user_settings(uid) if uid else {}
        user_tf = (us.get("timeframe") or CFG["analyze_timeframe"]).strip()
        user_locale = (us.get("locale") or CFG.get("default_locale", "uk")).strip().lower()
        if user_locale not in ("uk", "ua", "en"):
            user_locale = "uk"
        
        await _send(update, context, f"⏳ Аналізую всі монети на TF={user_tf}…")
        
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
                stop = _safe_float(plan.get("stop"))
                tp = _safe_float(plan.get("tp"))
                conf = _safe_float(plan.get("confidence")) or 0.0
                
                rr_num = _compute_rr_num(
                    direction,
                    entry if entry is not None else math.nan,
                    stop if stop is not None else math.nan,
                    tp if tp is not None else math.nan
                )
                rr_text = f"{rr_num:.2f}" if rr_num is not None else "-"
                
                try:
                    rr_min = float(us.get("rr_threshold", CFG.get("rr_threshold", 1.5)))
                    if rr_num is not None and rr_num < rr_min:
                        await _send(update, context, f"⚠️ {symbol} скіп (RR < {rr_min}).")
                        indi_md = format_ta_report(symbol, user_tf, CFG["analyze_limit"])
                        await _send(update, context, "📈 Indicators (preset):\n" + indi_md, parse_mode="Markdown")
                        continue
                except Exception:
                    pass
                
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
                    f"Entry: {_fmt_or_dash(entry)}\n"
                    f"Stop:  {_fmt_or_dash(stop)}\n"
                    f"Take:  {_fmt_or_dash(tp)}\n"
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
    """Handle analyze refresh callback."""
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
    """Handle goto panel callback."""
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
