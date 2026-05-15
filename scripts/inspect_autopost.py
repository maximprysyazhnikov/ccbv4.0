#!/usr/bin/env python3
import sqlite3

DB='storage/bot.db'
conn=sqlite3.connect(DB)
cur=conn.cursor()
print('schema autopost_log:')
for r in cur.execute("PRAGMA table_info(autopost_log);").fetchall():
    print(r)

print('\nrows for ETHUSDT:')
for r in cur.execute("SELECT * FROM autopost_log WHERE symbol='ETHUSDT' ORDER BY id").fetchall():
    print(r)

print('\nlast 10 autopost_log rows:')
for r in cur.execute('SELECT * FROM autopost_log ORDER BY id DESC LIMIT 10').fetchall():
    print(r)

conn.close()