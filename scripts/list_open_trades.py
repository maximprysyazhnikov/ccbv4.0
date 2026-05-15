import sqlite3
conn=sqlite3.connect('storage/bot.db')
cur=conn.cursor()
rows=cur.execute("select id,symbol,direction,status,trade_mode,gate_score,gate_pct from trades where status='OPEN' order by id").fetchall()
print('OPEN TRADES:')
for r in rows:
    print(r)
conn.close()