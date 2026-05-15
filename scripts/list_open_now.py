import sqlite3
c=sqlite3.connect('storage/bot.db')
for r in c.execute("select id,symbol,direction,status,trade_mode,opened_at,closed_at,pnl_usd from trades where status='OPEN' order by id"):
    print(r)
c.close()
