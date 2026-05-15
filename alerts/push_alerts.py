from __future__ import annotations

import os
import sqlite3
import logging
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Optional, List

from utils.settings import get_setting

log = logging.getLogger("alerts")

DB_PATH = os.getenv("DB_PATH", "storage/bot.db")
STATE_PATH = Path("alerts/runtime_state.json")

@dataclass
class AlertCfg:
    tz_name: str = "Europe/Kyiv"
    chat_id: str = ""
    max_consec_losses: int = 4
    drawdown_alert_r: float = 0.05   # інтерпретуємо як абсолют у R (наприклад 0.05R)
    wr_window: int = 20
    wr_min: float = 0.4              # 40%
    wr_min_closed: int = 20          # do not alert until the window is actually filled
    repeat_min: int = 180            # загальний cooldown для повтору тих самих risk alerts
    wr_repeat_min: int = 180         # не спамити low-WR кожні 5 хв

def _cfg() -> AlertCfg:
    tz = get_setting("tz_name", "Europe/Kyiv") or "Europe/Kyiv"
    chat = get_setting("telegram_chat_id", "") or os.getenv("TELEGRAM_CHAT_ID", "")
    mcl = int(float(get_setting("max_consecutive_losses", "4") or 4))
    dd = float(get_setting("drawdown_alert_pct", "0.05") or 0.05)
    wrw = int(float(get_setting("wr_window", "20") or 20))
    wrmin = float(get_setting("wr_min", "0.4") or 0.4)
    wr_min_closed = int(float(get_setting("wr_min_closed", str(wrw)) or wrw))
    repeat_min = int(float(get_setting("alerts_repeat_min", "180") or 180))
    wr_repeat_min = int(float(get_setting("wr_repeat_min", "180") or 180))
    return AlertCfg(tz_name=tz, chat_id=chat, max_consec_losses=mcl, drawdown_alert_r=dd,
                    wr_window=wrw, wr_min=wrmin, wr_min_closed=wr_min_closed,
                    repeat_min=repeat_min, wr_repeat_min=wr_repeat_min)

def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def _should_send_wr_alert(state: dict, *, wr: float, now: datetime, repeat_min: int) -> bool:
    node = state.get("low_wr") or {}
    active = bool(node.get("active"))
    last_wr = float(node.get("wr", 1.0) or 1.0)
    last_sent_iso = node.get("last_sent_at")
    if not active:
        return True
    if wr < (last_wr - 0.02):
        return True
    if not last_sent_iso:
        return True
    try:
        last_sent = datetime.fromisoformat(last_sent_iso)
    except Exception:
        return True
    return now >= last_sent + timedelta(minutes=repeat_min)

def _should_send_simple_alert(state: dict, key: str, *, value: float, now: datetime, repeat_min: int) -> bool:
    node = state.get(key) or {}
    active = bool(node.get("active"))
    last_value = float(node.get("value", 0.0) or 0.0)
    last_sent_iso = node.get("last_sent_at")
    if not active:
        return True
    if value > last_value:
        return True
    if value < last_value - 0.02:
        return True
    if not last_sent_iso:
        return True
    try:
        last_sent = datetime.fromisoformat(last_sent_iso)
    except Exception:
        return True
    return now >= last_sent + timedelta(minutes=repeat_min)

def _mark_simple_alert_sent(state: dict, key: str, *, value: float, now: datetime) -> None:
    state[key] = {
        "active": True,
        "value": value,
        "last_sent_at": now.isoformat(),
    }

def _clear_simple_alert_state(state: dict, key: str) -> None:
    node = state.get(key) or {}
    if node.get("active"):
        state[key] = {
            "active": False,
            "value": 0.0,
            "last_sent_at": node.get("last_sent_at"),
        }

def _mark_wr_alert_sent(state: dict, *, wr: float, wins: int, losses: int, now: datetime) -> None:
    state["low_wr"] = {
        "active": True,
        "wr": wr,
        "wins": wins,
        "losses": losses,
        "last_sent_at": now.isoformat(),
    }

def _clear_wr_alert_state(state: dict) -> None:
    node = state.get("low_wr") or {}
    if node.get("active"):
        state["low_wr"] = {
            "active": False,
            "wr": 1.0,
            "wins": 0,
            "losses": 0,
            "last_sent_at": node.get("last_sent_at"),
        }

def _now_tz(tz_name: str) -> datetime:
    return datetime.now(ZoneInfo(tz_name))

def _day_bounds_utc(now_tz: datetime) -> tuple[int, int]:
    start = now_tz.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return int(start.astimezone(timezone.utc).timestamp()), int(end.astimezone(timezone.utc).timestamp())

def _week_bounds_utc(now_tz: datetime) -> tuple[int, int]:
    start = now_tz - timedelta(days=now_tz.weekday())  # Monday 00:00
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)
    return int(start.astimezone(timezone.utc).timestamp()), int(end.astimezone(timezone.utc).timestamp())

def _fetch_rr_between(cur: sqlite3.Cursor, start_ts: int, end_ts: int) -> List[float]:
    rows = cur.execute(
        "SELECT COALESCE(rr_realized, rr, pnl_usd, pnl, 0.0) FROM trades WHERE UPPER(COALESCE(status,''))!='OPEN' "
        "AND closed_at>=? AND closed_at<?",
        (start_ts, end_ts),
    ).fetchall()
    return [float(r[0] or 0.0) for r in rows]

def _consecutive_losses(cur: sqlite3.Cursor) -> int:
    """Рахує послідовні лоси. Використовує pnl_usd (реальний P/L), якщо rr=0."""
    rows = cur.execute(
        "SELECT COALESCE(rr_realized, rr, 0.0), COALESCE(pnl_usd, pnl, 0.0) FROM trades "
        "WHERE UPPER(COALESCE(status,''))!='OPEN' "
        "ORDER BY closed_at DESC LIMIT 200"
    ).fetchall()
    cnt = 0
    for (rr, pnl) in rows:
        # Використовуємо pnl_usd якщо rr=0
        value = float(rr) if float(rr) != 0 else float(pnl)
        if value <= 0.0:
            cnt += 1
        else:
            break
    return cnt

def _wr_window(cur: sqlite3.Cursor, n: int) -> tuple[int, int, float]:
    """Win rate за останні n угод. Використовує pnl_usd якщо rr=0."""
    if n <= 0:
        return 0, 0, 1.0
    rows = cur.execute(
        "SELECT COALESCE(rr_realized, rr, 0.0), COALESCE(pnl_usd, pnl, 0.0) FROM trades "
        "WHERE UPPER(COALESCE(status,''))!='OPEN' "
        "ORDER BY closed_at DESC LIMIT ?",
        (n,),
    ).fetchall()
    if not rows:
        return 0, 0, 1.0
    wins = 0
    for (rr, pnl) in rows:
        # Використовуємо pnl_usd якщо rr=0
        value = float(rr) if float(rr) != 0 else float(pnl)
        if value > 0.0:
            wins += 1
    return wins, len(rows), wins / len(rows)

def _sum_r(vals: List[float]) -> float:
    return sum(float(x or 0.0) for x in vals)

async def _send(bot, chat_id: str, text: str, parse_mode: str = None) -> None:
    if not chat_id:
        log.warning("[alerts] chat_id is empty, message: %s", text)
        return
    if bot is None:
        # якщо викликати з CLI / без бота — пишемо у лог
        log.info("[alerts] %s", text)
        return
    try:
        await bot.send_message(chat_id=chat_id, text=text, disable_web_page_preview=True, parse_mode=parse_mode)
    except Exception as e:
        log.warning("[alerts] send fail: %s", e)

async def run_alerts_once(bot=None) -> int:
    """
    Перевіряє:
      • N підряд лосів
      • Дроудаун дня / тижня (сума R за період)
      • Падіння WR% за останні X угод
    Надсилає алерти у TG. Повертає кількість тригерів.
    """
    cfg = _cfg()
    now = _now_tz(cfg.tz_name)
    day_s, day_e = _day_bounds_utc(now)
    week_s, week_e = _week_bounds_utc(now)

    fired = 0
    state = _load_state()
    with sqlite3.connect(DB_PATH) as con:
        cur = con.cursor()

        # consecutive losses
        cl = _consecutive_losses(cur)
        if cl >= cfg.max_consec_losses:
            if _should_send_simple_alert(state, "consec_losses", value=float(cl), now=now, repeat_min=cfg.repeat_min):
                fired += 1
                await _send(
                    bot, cfg.chat_id,
                    f"⚠️ <b>ALERT: Серія лосів</b>\n\n"
                    f"🔴 <b>{cl}</b> лосів підряд (ліміт: {cfg.max_consec_losses})\n\n"
                    f"💡 <i>Рекомендація: зроби паузу, проаналізуй останні угоди.</i>",
                    parse_mode="HTML"
                )
                _mark_simple_alert_sent(state, "consec_losses", value=float(cl), now=now)
        else:
            _clear_simple_alert_state(state, "consec_losses")

        # daily drawdown (в R)
        day_rr = _sum_r(_fetch_rr_between(cur, day_s, day_e))
        if day_rr <= -abs(cfg.drawdown_alert_r):
            day_dd = abs(day_rr)
            if _should_send_simple_alert(state, "day_drawdown", value=day_dd, now=now, repeat_min=cfg.repeat_min):
                fired += 1
                await _send(
                    bot, cfg.chat_id,
                    f"📉 <b>ALERT: Денний дроудаун</b>\n\n"
                    f"💰 Сьогодні: <b>{day_rr:.2f}R</b> (ліміт: -{cfg.drawdown_alert_r}R)\n\n"
                    f"💡 <i>Денний ліміт збитків перевищено.</i>",
                    parse_mode="HTML"
                )
                _mark_simple_alert_sent(state, "day_drawdown", value=day_dd, now=now)
        else:
            _clear_simple_alert_state(state, "day_drawdown")

        # weekly drawdown (в R)
        week_rr = _sum_r(_fetch_rr_between(cur, week_s, week_e))
        if week_rr <= -abs(cfg.drawdown_alert_r):
            week_dd = abs(week_rr)
            if _should_send_simple_alert(state, "week_drawdown", value=week_dd, now=now, repeat_min=cfg.repeat_min):
                fired += 1
                await _send(
                    bot, cfg.chat_id,
                    f"📉 <b>ALERT: Тижневий дроудаун</b>\n\n"
                    f"💰 Цього тижня: <b>{week_rr:.2f}R</b> (ліміт: -{cfg.drawdown_alert_r}R)\n\n"
                    f"💡 <i>Тижневий ліміт збитків перевищено.</i>",
                    parse_mode="HTML"
                )
                _mark_simple_alert_sent(state, "week_drawdown", value=week_dd, now=now)
        else:
            _clear_simple_alert_state(state, "week_drawdown")

        # WR% last N trades. During a fresh DB / recovery warmup, avoid fake
        # "0W / 20L" alerts when only a few trades have actually closed.
        wins, wr_total, wr = _wr_window(cur, cfg.wr_window)
        losses = max(0, wr_total - wins)
        if wr_total < cfg.wr_min_closed:
            _clear_wr_alert_state(state)
            log.info("[alerts] wr warmup %d/%d closed trades", wr_total, cfg.wr_min_closed)
        elif wr < cfg.wr_min:
            if _should_send_wr_alert(state, wr=wr, now=now, repeat_min=cfg.wr_repeat_min):
                fired += 1
                await _send(
                    bot, cfg.chat_id,
                    f"❗ <b>ALERT: Низький Win Rate</b>\n\n"
                    f"📊 WR = <b>{wr*100:.1f}%</b> (мін. {cfg.wr_min*100:.0f}%)\n"
                    f"📈 Останні {wr_total} угод: {wins}W / {losses}L\n\n"
                    f"💡 <i>Win Rate нижче порогу — розглянь паузу або перегляд стратегії.</i>",
                    parse_mode="HTML"
                )
                _mark_wr_alert_sent(state, wr=wr, wins=wins, losses=losses, now=now)
        else:
            _clear_wr_alert_state(state)

    _save_state(state)
    if fired:
        log.info("[alerts] fired=%d (day=%.2fR, week=%.2fR, wr=%.1f%%, consec=%d)",
                 fired, day_rr, week_rr, wr*100, cl)
    else:
        log.info("[alerts] no triggers")
    return fired
