import sqlite3
conn=sqlite3.connect('storage/bot.db')
cur=conn.cursor()
ids=(72,73,74,75)
rows=cur.execute('select id,symbol,direction,status,trade_mode,opened_at,closed_at,pnl_usd from trades where id in ({}) order by id'.format(','.join(str(i) for i in ids))).fetchall()
for r in rows:
    print(r)
conn.close()