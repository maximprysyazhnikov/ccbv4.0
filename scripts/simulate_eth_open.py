#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from services.autopost_bridge import handle_autopost_message
import sqlite3

MSG = """
🤖 Autopost plan ETHUSDT [5m]
🔴 SHORT | RR:3.50
🔖 Попав у: СКАЛЬП
Entry: 2 692.21 | SL: 2 702.44 | TP: 2 662.06
"""

print('Message to parse:\n', MSG)
trade_id = handle_autopost_message({"text": MSG, "meta": {}})
print('Returned trade_id:', trade_id)
if trade_id:
    conn = sqlite3.connect('storage/bot.db')
    cur = conn.cursor()
    row = cur.execute('SELECT id,symbol,trade_mode,status,entry,sl,tp FROM trades WHERE id=?', (trade_id,)).fetchone()
    print('Trade row:', row)
    conn.close()
else:
    print('No trade opened (bridge skipped or rr too low)')
