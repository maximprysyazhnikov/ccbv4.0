"""Message formatting functions for autopost."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from utils.settings import get_setting


def fmt_num(x: float, decimals: int = 2) -> str:
    """Format number with commas."""
    return f"{x:,.{decimals}f}".replace(",", " ")


def fmt_price(p: Optional[float]) -> str:
    """Format price."""
    if p is None:
        return "-"
    decimals = 2 if abs(p) >= 100 else 6
    return fmt_num(float(p), decimals)


def fmt_pct(x: Optional[float]) -> str:
    """Format percentage."""
    if x is None:
        return "-"
    return f"{float(x):.2f}%"


def safe_float(x):
    """Safely convert to float."""
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except Exception:
        return None


def _mode_label(trade_mode: Optional[str]) -> str:
    return "СКАЛЬП" if trade_mode and str(trade_mode).lower() == "scalping" else "КЛАСИЧНИЙ"


def format_message_text(
    symbol: str,
    direction: str,
    timeframe: str,
    entry: float,
    sl: float,
    tp: Optional[float],
    rr_t: Optional[float],
    ind_sum: Dict[str, Any],
    gate_score: Optional[int],
    gate_total: Optional[int],
    panel: Optional[str] = None,
    reasons: Optional[List[str]] = None,
    qscore: Optional[int] = None,
    qtags: Optional[List[str]] = None,
    ob: Optional[Dict[str, Any]] = None,
    ob_extra_lines: Optional[List[str]] = None,
    sentiment: Optional[Dict[str, Any]] = None,  # 📊 Long/Short Ratio
    full_ind: Optional[Dict[str, Any]] = None,  # Full indicator data from scalping_sources
    trade_mode: Optional[str] = None,  # 'scalping' | 'standard' | 'ai'
) -> str:
    """Format autopost message text with full analytics."""
    from datetime import datetime, timezone
    
    # Header with direction emoji
    dir_emoji = "🟢" if direction == "LONG" else "🔴"
    rr_text = "-" if rr_t is None else f"{float(rr_t):.2f}"
    
    # Calculate SL/TP distances
    sl_dist_pct = abs(entry - sl) / entry * 100 if entry > 0 else 0
    tp_dist_pct = abs(tp - entry) / entry * 100 if tp and entry > 0 else 0
    
    # Keep these two lines parser-friendly for autopost_bridge.
    parser_header = f"🤖 Autopost plan {symbol} [{timeframe}]"
    parser_levels = f"Entry: {fmt_price(entry)} | SL: {fmt_price(sl)} | TP: {('-' if tp is None else fmt_price(tp))}"

    mode_txt = _mode_label(trade_mode)
    
    e_txt = fmt_price(entry)
    s_txt = fmt_price(sl)
    t_txt = "-" if tp is None else fmt_price(tp)
    
    # Gate score visual
    if gate_score is not None and gate_total is not None:
        gate_pct = gate_score / gate_total * 100 if gate_total > 0 else 0
        if gate_pct >= 70:
            gate_emoji = "🟢"
        elif gate_pct >= 50:
            gate_emoji = "🟡"
        else:
            gate_emoji = "🔴"
        gate_txt = f"{gate_emoji} {gate_score}/{gate_total} ({gate_pct:.0f}%)"
    else:
        gate_txt = "–"
    
    lines = [
        f"{dir_emoji} {symbol} · {direction} · {timeframe}",
        f"🔖 {mode_txt} | ⚖️ RR {rr_text} | 🚦 Gate {gate_txt}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📍 Entry  {e_txt}",
        f"🛑 SL     {s_txt}  ({sl_dist_pct:.2f}%)",
        f"🎯 TP     {t_txt}  ({tp_dist_pct:.2f}%)",
        "━━━━━━━━━━━━━━━━━━━━",
        parser_header,
        parser_levels,
    ]
    
    # Full indicator display if available
    if full_ind and isinstance(full_ind, dict):
        lines.append("📊 Індикатори")
        
        # Trend
        ema50 = full_ind.get("ema50")
        ema200 = full_ind.get("ema200")
        if ema50 is not None and ema200 is not None:
            trend_ok = (ema50 >= ema200) if direction == "LONG" else (ema50 < ema200)
            lines.append(f"{'✅' if trend_ok else '❌'} EMA50/200  {ema50:.4f} / {ema200:.4f}")
        
        # RSI
        rsi = full_ind.get("rsi14")
        if rsi is not None:
            rsi_emoji = "🟢" if 40 <= rsi <= 60 else ("🟡" if 30 <= rsi <= 70 else "🔴")
            lines.append(f"{rsi_emoji} RSI14      {rsi:.1f}")
        
        # MACD
        macd_hist = full_ind.get("macd_hist")
        if macd_hist is not None:
            macd_ok = (macd_hist > 0) if direction == "LONG" else (macd_hist < 0)
            lines.append(f"{'✅' if macd_ok else '❌'} MACD hist  {macd_hist:+.4f}")
        
        # ADX
        adx = full_ind.get("adx14")
        if adx is not None:
            adx_emoji = "💪" if adx >= 25 else ("🟡" if adx >= 20 else "⚪")
            lines.append(f"{adx_emoji} ADX14      {adx:.1f}")
        
        # StochRSI
        stoch_k = full_ind.get("stoch_k")
        stoch_d = full_ind.get("stoch_d")
        if stoch_k is not None and stoch_d is not None:
            stoch_ok = (stoch_k > stoch_d) if direction == "LONG" else (stoch_k < stoch_d)
            lines.append(f"{'✅' if stoch_ok else '❌'} StochRSI  K {stoch_k:.1f} / D {stoch_d:.1f}")
        
        # Bollinger %B
        bb_pct_b = full_ind.get("bb_pct_b")
        if bb_pct_b is not None:
            if bb_pct_b > 0.8:
                bb_emoji = "🔴"  # перекуплено
            elif bb_pct_b < 0.2:
                bb_emoji = "🔴"  # перепродано
            else:
                bb_emoji = "🟢"
            lines.append(f"{bb_emoji} BB%B       {bb_pct_b:.2f}")
        
        # ATR
        atr_pct = full_ind.get("atr_pct")
        if atr_pct is not None:
            lines.append(f"📈 ATR        {atr_pct:.2f}%")
        
        # VWAP
        vwap_delta = full_ind.get("vwap_delta_pct")
        if vwap_delta is not None:
            lines.append(f"🎯 VWAPΔ      {vwap_delta:+.2f}%")
        
        # Volume
        vol_ratio = full_ind.get("vol_ratio")
        if vol_ratio is not None:
            vol_emoji = "📊" if vol_ratio >= 1.0 else "📉"
            lines.append(f"{vol_emoji} Volume     {vol_ratio:.2f}x")
        
        # MFI
        mfi = full_ind.get("mfi14")
        if mfi is not None:
            mfi_emoji = "🟢" if 20 < mfi < 80 else "🔴"
            lines.append(f"{mfi_emoji} MFI14      {mfi:.1f}")
        
        # CCI
        cci = full_ind.get("cci20")
        if cci is not None:
            cci_emoji = "🟢" if -100 < cci < 100 else "🟡"
            lines.append(f"{cci_emoji} CCI20      {cci:.1f}")
        
        lines.append("━━━━━━━━━━━━━━━━━━━━")
    else:
        # Fallback to old ind_sum format
        atr_min_pct = float(get_setting("autopost_min_atr_pct", "0") or 0.0)
        vwap_min_pct = float(get_setting("vwap_dist_min", "0") or 0.0)
        
        if direction == "LONG":
            rsi_thr = float(get_setting("rsi_long_min", "50") or 50)
            rsi_cmp = f"≥{int(rsi_thr)}?"
            rsi_ok = (ind_sum.get("rsi") is not None and ind_sum["rsi"] >= rsi_thr)
        else:
            rsi_thr = float(get_setting("rsi_short_max", "50") or 50)
            rsi_cmp = f"≤{int(rsi_thr)}?"
            rsi_ok = (ind_sum.get("rsi") is not None and ind_sum["rsi"] <= rsi_thr)
        
        trend_flag = ind_sum.get("trend_ok")
        atr_pct = ind_sum.get("atr_pct")
        vwap_pct = ind_sum.get("vwap_pct")
        
        atr_ok = None if atr_pct is None else (atr_pct >= atr_min_pct)
        vwap_ok = None if vwap_pct is None else ((vwap_pct >= vwap_min_pct) if vwap_min_pct > 0 else True)
        
        rsi_val = "-" if ind_sum.get("rsi") is None else f"{ind_sum['rsi']:.1f}"
        
        lines += [
            "🧭 Тренд: EMA50≥EMA200 " + ("✅" if trend_flag is True else ("❌" if trend_flag is False else "–")),
            "📊 ATR: " + fmt_pct(atr_pct) + (" ✅" if atr_ok is True else (" ❌" if atr_ok is False else " –")),
            "🎯 VWAPΔ: " + fmt_pct(vwap_pct) + (" ✅" if vwap_ok is True else (" ❌" if vwap_ok is False else " –")),
            f"💪 RSI14: {rsi_val} ({rsi_cmp}) " + ("✅" if rsi_ok else ("❌" if ind_sum.get('rsi') is not None else "–")),
        ]
    
    # Pivot levels if available
    if full_ind:
        pivot = full_ind.get("pivot")
        r1 = full_ind.get("r1")
        s1 = full_ind.get("s1")
        if pivot and r1 and s1:
            lines.append(f"📐 Pivots  S1 {fmt_price(s1)} | P {fmt_price(pivot)} | R1 {fmt_price(r1)}")
    
    # Reasons (gate details)
    if reasons and isinstance(reasons, list) and len(reasons) > 0:
        # Filter out emoji reasons for cleaner output, show summary
        clean_reasons = [r for r in reasons if "─────" not in r and "GATE:" not in r and "SCALP" not in r]
        passed_count = sum(1 for r in clean_reasons if r.startswith("✅"))
        failed_count = sum(1 for r in clean_reasons if r.startswith("❌"))
        if passed_count > 0 or failed_count > 0:
            lines.append(f"✅ Пройшло {passed_count} | ❌ Не пройшло {failed_count}")
    
    if panel:
        lines += ["", panel]
    
    # OrderBook walls
    if ob:
        sup = ob.get("support_wall")
        res = ob.get("resistance_wall")
        if sup or res:
            lines.append("🧱 Walls:")
            if sup:
                lines.append(f"  📗 Support @{sup['price']:.2f} ({sup['vol_str']}) {sup['dist_str']}")
            if res:
                lines.append(f"  📕 Resistance @{res['price']:.2f} ({res['vol_str']}) {res['dist_str']}")
        if ob.get("imbalance") is not None:
            imb = ob['imbalance']
            imb_emoji = "🟢" if imb > 1 else ("🔴" if imb < 1 else "⚪")
            lines.append(f"{imb_emoji} Imbalance: {imb:.2f}")
    
    if ob_extra_lines:
        lines += ["📚 OrderBook:"]
        lines += ["  • " + s for s in ob_extra_lines[:3]]
    
    if isinstance(qtags, str):
        qtags = qtags.split()
    
    if qscore is not None:
        tags_str = " ".join(qtags or [])
        q_emoji = "⭐⭐⭐" if qscore >= 80 else ("⭐⭐" if qscore >= 60 else "⭐")
        lines.append(f"{q_emoji} Quality {qscore}/100 ({tags_str})")
    
    # 📊 Long/Short Ratio (Sentiment)
    if sentiment:
        from market_data.long_short_ratio import format_sentiment_line
        sent_line = format_sentiment_line(sentiment)
        if sent_line:
            lines.append(sent_line)
    
    lines.append(f"⏱ {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC")
    return "\n".join(lines)
