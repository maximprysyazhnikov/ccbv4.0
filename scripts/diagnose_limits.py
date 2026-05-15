import sqlite3

conn=sqlite3.connect('storage/bot.db')
cur=conn.cursor()
# list all settings
cur.execute("SELECT key,value FROM settings ORDER BY key")
rows=cur.fetchall()
print('settings rows:', rows)
# list tables
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
print('tables:', [r[0] for r in cur.fetchall()])
# try trades queries - inspect schema and some rows
cur.execute("PRAGMA table_info(trades)")
print('trades schema:', cur.fetchall())
try:
    cur.execute("SELECT id, symbol, opened_at, closed_at, status, entry, close_price FROM trades ORDER BY id DESC LIMIT 20")
    for r in cur.fetchall():
        print('trade row:', r)
    cur.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN'")
    open_count=cur.fetchone()[0]
    print('open_count (status=OPEN)=', open_count)
    cur.execute("SELECT COUNT(*) FROM trades WHERE opened_at >= datetime('now','-24 hours')")
    last24=cur.fetchone()[0]
    print('opened_last_24h=', last24)
except Exception as e:
    print('trades query failed:', e)
# check autopost_log and candidates schema
for t in ('autopost_log','autopost_candidates','user_settings'):
    try:
        cur.execute(f"PRAGMA table_info({t})")
        print(f'{t} schema:', cur.fetchall())
    except Exception as e:
        print(f'{t} schema query failed:', e)
# list user_settings rows
try:
    cur.execute("SELECT * FROM user_settings ORDER BY rowid DESC LIMIT 50")
    for r in cur.fetchall():
        print('user_setting row:', r)
except Exception as e:
    print('user_settings query failed:', e)
# list autopost_log rows (limited)
try:
    cur.execute("SELECT rowid, * FROM autopost_log ORDER BY rowid DESC LIMIT 20")
    for r in cur.fetchall():
        print('autopost_log row:', r)
except Exception as e:
    print('autopost_log query failed:', e)
# list autopost_candidates rows
try:
    cur.execute("SELECT rowid, * FROM autopost_candidates ORDER BY rowid DESC LIMIT 20")
    for r in cur.fetchall():
        print('autopost_candidate row:', r)
except Exception as e:
    print('autopost_candidates query failed:', e)
cur.close(); conn.close()
