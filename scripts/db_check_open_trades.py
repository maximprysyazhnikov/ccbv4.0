import sqlite3

DB='storage/bot.db'
# Try normal connection; fall back to read-only if DB locked
try:
    conn=sqlite3.connect(DB, timeout=5)
    cur=conn.cursor()
    cols=[r[1] for r in cur.execute("PRAGMA table_info('trades')").fetchall()]
except Exception as e:
    print('Normal open failed:', e)
    print('Retrying read-only...')
    conn=sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=5)
    cur=conn.cursor()
    cols=[r[1] for r in cur.execute("PRAGMA table_info('trades')").fetchall()]

print('COLUMNS:', cols)
rows=cur.execute(f"select {', '.join(cols)} from trades order by id desc limit 200").fetchall()
if 'closed_at' in cols:
    closed_idx=cols.index('closed_at')
    open_trades=[r for r in rows if r[closed_idx] is None]
elif 'status' in cols:
    status_idx=cols.index('status')
    open_trades=[r for r in rows if r[status_idx] != 'closed']
else:
    open_trades=rows
print('OPEN_COUNT=', len(open_trades))
for r in open_trades[::-1]:
    # print id, symbol and status/closed_at
    id_idx=cols.index('id') if 'id' in cols else 0
    sym_idx=cols.index('symbol') if 'symbol' in cols else 1
    s='';
    if 'status' in cols:
        s=f"status={r[cols.index('status')] }"
    elif 'closed_at' in cols:
        s=f"closed_at={r[cols.index('closed_at')] }"
    print('id=', r[id_idx], 'symbol=', r[sym_idx], s)
conn.close()