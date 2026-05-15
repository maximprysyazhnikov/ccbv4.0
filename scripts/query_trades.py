#!/usr/bin/env python3
import sqlite3
import sys

DB='storage/bot.db'
try:
    conn=sqlite3.connect(DB)
    cur=conn.cursor()
    rows=cur.execute("SELECT symbol, COALESCE(trade_mode,''), status, opened_at FROM trades WHERE opened_at >= date('now','-7 day') ORDER BY opened_at DESC LIMIT 200").fetchall()
    if not rows:
        print('NO_ROWS')
    else:
        for r in rows:
            print(r)
except Exception as e:
    print('ERROR', e)
    sys.exit(1)
