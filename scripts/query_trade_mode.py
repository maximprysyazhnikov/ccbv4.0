import sqlite3
DB='storage/bot.db'
conn=sqlite3.connect(DB)
cur=conn.cursor()
for tid in (72,73):
    r=cur.execute('select id,symbol,direction,status,trade_mode,gate_score,gate_total,gate_pct,rr_raw,rr_adj,opened_at from trades where id=?',(tid,)).fetchone()
    print('\nTRADE:', r)
conn.close()