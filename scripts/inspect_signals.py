#!/usr/bin/env python3
import sqlite3

DB='storage/bot.db'
conn=sqlite3.connect(DB)
cur=conn.cursor()
print('signals for ETHUSDT:')
for r in cur.execute("SELECT id,user_id,symbol,timeframe,direction,entry,sl,tp,rr,status,opened_at FROM signals WHERE symbol='ETHUSDT' ORDER BY id DESC LIMIT 10").fetchall():
    print(r)

print('\nlast 10 signals:')
for r in cur.execute('SELECT id,user_id,symbol,timeframe,direction,entry,sl,tp,rr,status,opened_at FROM signals ORDER BY id DESC LIMIT 10').fetchall():
    print(r)

conn.close()