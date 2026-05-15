"""Автоматично змінює quality_gate_pct для користувача і перевіряє кандидати скальпінгу.
Запуск:
    python scripts/auto_set_qg_and_check.py --qg 50
"""
from __future__ import annotations
import argparse
import os
import sys
sys.path.insert(0, str((__file__).replace('\\scripts\\auto_set_qg_and_check.py','')))

from utils.user_settings import get_user_settings, set_user_settings
from utils.settings import get_setting

import asyncio

async def run_check():
    from services.scalping_sources import collect_scalping_candidates
    candidates = await collect_scalping_candidates()
    return candidates


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--qg', type=float, default=50.0, help='New quality_gate_pct value')
    args = p.parse_args()

    user_id = os.getenv('TELEGRAM_CHAT_ID', '1126438536')
    try:
        orig = get_user_settings(user_id)
        prev = orig.get('quality_gate_pct') if isinstance(orig, dict) else None
    except Exception:
        prev = None

    print(f"User {user_id} previous quality_gate_pct={prev} -> setting to {args.qg}")
    set_user_settings(user_id, quality_gate_pct=float(args.qg))

    print("Re-running scalping candidates check...")
    candidates = asyncio.run(run_check())

    print(f"Found {len(candidates)} candidates")
    for c in candidates:
        sym = c.get('symbol')
        gate = f"{c.get('gate_score')}/{c.get('gate_total')} ({c.get('gate_pct'):.0f}%)"
        rr = c.get('rr_adj') or c.get('rr_target') or c.get('rr_raw')
        print(f"{sym}: gate={gate} RR={rr} PASS={'yes' if (int(c.get('gate_score') or 0) >= 0) else 'no'}")

if __name__ == '__main__':
    main()