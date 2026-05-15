import sqlite3
s='BNBUSDT'
conn=sqlite3.connect('storage/bot.db')
cur=conn.cursor()
print('--- settings ---')
for k in ('quality_gate_pct','indicator_min_pass','max_open_per_run','max_open_per_day'):
    try:
        cur.execute('SELECT value FROM settings WHERE key=?',(k,))
        r=cur.fetchone()
        print(k, '=>', r[0] if r else None)
    except Exception as e:
        print('err reading settings',k,e)

print('\n--- user_settings row (autopost) ---')
cur.execute("SELECT * FROM user_settings WHERE user_id=?", (1126438536,))
print(cur.fetchone())

print('\n--- autopost_log recent for',s,'---')
cur.execute("SELECT rowid, user_id, symbol, timeframe, rr, ts_sent, ts FROM autopost_log WHERE symbol=? ORDER BY rowid DESC LIMIT 20", (s,))
for r in cur.fetchall():
    print(r)

print('\n--- autopost_candidates for',s,'---')
cur.execute("SELECT rowid, user_id, symbol, timeframe, consecutive_passes, last_pass_ts FROM autopost_candidates WHERE symbol=? ORDER BY rowid DESC LIMIT 20", (s,))
for r in cur.fetchall():
    print(r)

print('\n--- recent trades for',s,'---')
cur.execute("SELECT id, symbol, status, direction, opened_at, closed_at FROM trades WHERE symbol=? ORDER BY id DESC LIMIT 20", (s,))
for r in cur.fetchall():
    print(r)

cur.close(); conn.close()