#!/usr/bin/env python3
"""Simulate an external autopost message being bridged (handle_autopost_message)

Usage: python scripts/simulate_bridge_open.py --text-file msg.txt
If no file provided, uses built-in SOLUSDT message with mode line.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
try:
    from services.autopost_bridge import handle_autopost_message
except Exception:
    import traceback
    traceback.print_exc()
    raise
import sqlite3
import argparse

DEFAULT_MSG = """
🤖 Autopost plan SOLUSDT [5m]
🔵 LONG | RR:3.50
🔖 Попав у: СКАЛЬП
Entry: 118.46 | SL: 118.05 | TP: 120.18
"""

parser = argparse.ArgumentParser()
parser.add_argument("--text-file", help="Path to txt file with message")
args = parser.parse_args()

text = DEFAULT_MSG
if args.text_file:
    with open(args.text_file, 'r', encoding='utf-8') as f:
        text = f.read()

print('Message to parse:\n', text)
trade_id = handle_autopost_message({"text": text, "meta": {}})
print('Returned trade_id:', trade_id)
if trade_id:
    conn = sqlite3.connect('storage/bot.db')
    cur = conn.cursor()
    row = cur.execute('SELECT id,symbol,trade_mode,status,entry,sl,tp FROM trades WHERE id=?', (trade_id,)).fetchone()
    print('Trade row:', row)
    conn.close()
else:
    print('No trade opened (bridge skipped or rr too low)')
