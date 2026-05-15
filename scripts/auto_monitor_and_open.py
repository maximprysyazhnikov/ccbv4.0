"""Автоматичний монітор та відкриття трейдів для PASS кандидтів (скальпінг)

Логіка:
- Встановлює quality_gate_pct (за потреби)
- Періодично (інтервал) збирає кандидати скальпінгу
- Для кожного кандидата що проходить gate відкриває трейд idempotently
- Моніторить логи на помилки типу "'volume'"; завершує роботу якщо помилок немає певну кількість перевірок підряд
- Логування у консоль

Запуск:
    python scripts/auto_monitor_and_open.py --qg 50 --interval 30 --max-iterations 20

"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
import time
from typing import List

# ensure project root on path
sys.path.insert(0, str((__file__).replace('\\scripts\\auto_monitor_and_open.py','')))

from utils.user_settings import get_user_settings, set_user_settings
from services.autopost.core import _gate_ok
from services.trade_engine import open_trade_from_signal

LOG_FILE = os.path.join(os.getcwd(), 'logs', 'app.log')

def count_volume_errors_in_log() -> int:
    if not os.path.exists(LOG_FILE):
        return 0
    cnt = 0
    try:
        with open(LOG_FILE, 'r', encoding='utf-8', errors='ignore') as fh:
            for line in fh:
                if "'volume'" in line or "Error processing" in line and "volume" in line:
                    cnt += 1
    except Exception:
        return 0
    return cnt

async def get_candidates() -> List[dict]:
    from services.scalping_sources import collect_scalping_candidates
    return await collect_scalping_candidates()

def try_open(signal: dict) -> int | None:
    try:
        tid = open_trade_from_signal(signal, trade_mode='scalping')
        return tid
    except Exception as e:
        print(f"open_trade_from_signal failed for {signal.get('symbol')}: {e}")
        return None

async def main_loop(qg: float, interval: int, max_iterations: int, stable_checks_needed: int):
    user_id = os.getenv('TELEGRAM_CHAT_ID', '1126438536')
    prev = get_user_settings(user_id).get('quality_gate_pct') if isinstance(get_user_settings(user_id), dict) else None
    print(f"Set QG: {prev} -> {qg}")
    set_user_settings(user_id, quality_gate_pct=float(qg))

    stable_count = 0
    last_error_count = count_volume_errors_in_log()
    print(f"Initial volume-error count in log: {last_error_count}")

    for i in range(1, max_iterations+1):
        print(f"\n--- Iteration {i}/{max_iterations} ---")
        try:
            cands = await get_candidates()
        except Exception as e:
            print(f"Failed to collect candidates: {e}")
            cands = []

        print(f"Collected {len(cands)} candidates")

        opened = 0
        for c in cands:
            # check gate via autopost _gate_ok (uses user's QG)
            rr = c.get('rr_adj') or c.get('rr_target') or c.get('rr_raw')
            ok, reason = _gate_ok(c, rr, quality_gate_pct=float(qg))
            if not ok:
                print(f"SKIP {c.get('symbol')}: {reason}")
                continue
            # open trade
            tid = try_open(c)
            if tid:
                opened += 1
                print(f"Opened trade ID {tid} for {c.get('symbol')}")
            else:
                print(f"No trade opened for {c.get('symbol')} (maybe already open)")

        print(f"Iteration {i}: opened {opened} trades")

        # check log errors
        cur_errors = count_volume_errors_in_log()
        print(f"Volume-error count in log: {cur_errors}")
        if cur_errors == last_error_count:
            stable_count += 1
        else:
            stable_count = 0
        last_error_count = cur_errors

        if cur_errors == 0 and stable_count >= stable_checks_needed:
            print("No volume errors detected for several consecutive checks — assuming resolved.")
            break

        # sleep
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            break

    print("Finished monitoring loop. Restoring previous QG setting.")
    if prev is not None:
        try:
            set_user_settings(user_id, quality_gate_pct=float(prev))
            print(f"Restored QG to {prev}")
        except Exception:
            pass


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--qg', type=float, default=50.0)
    p.add_argument('--interval', type=int, default=30)
    p.add_argument('--max-iterations', type=int, default=20)
    p.add_argument('--stable-checks', type=int, default=3, help='Consecutive checks without new errors to consider resolved')
    args = p.parse_args()

    try:
        asyncio.run(main_loop(args.qg, args.interval, args.max_iterations, args.stable_checks))
    except KeyboardInterrupt:
        print('Interrupted by user')
