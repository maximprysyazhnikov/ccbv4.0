import sys, os
# Ensure repo root on sys.path when running as script
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import asyncio
from services.autopost.core import run_autopost_once
from services.autopost import mark_autopost_sent
from services.autopost_bridge import handle_autopost_message
from services.autopost.persistence import get_candidate_passes
from utils.db import get_conn


def print_autopost_log(limit=10):
    with get_conn() as c:
        rows = c.execute("SELECT rowid, user_id, symbol, timeframe, ts, ts_sent, rr FROM autopost_log ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        print('\n--- autopost_log (recent) ---')
        for r in rows:
            print(r)


def print_candidates():
    with get_conn() as c:
        rows = c.execute("SELECT user_id, symbol, timeframe, consecutive_passes, last_pass_ts FROM autopost_candidates ORDER BY last_pass_ts DESC LIMIT 20").fetchall()
        print('\n--- autopost_candidates ---')
        for r in rows:
            print(r)


def print_open_trades():
    with get_conn() as c:
        rows = c.execute("SELECT id,symbol,direction,status,opened_at,closed_at FROM trades WHERE status='OPEN' ORDER BY id").fetchall()
        print('\n--- OPEN trades ---')
        for r in rows:
            print(r)


async def main():
    msgs = await run_autopost_once(None)
    print('Prepared messages:', len(msgs))
    for i, m in enumerate(msgs):
        if not isinstance(m, dict):
            continue
        symbol = m.get('symbol')
        timeframe = m.get('timeframe')
        chat_id = m.get('chat_id')
        rr = m.get('rr')
        print(f'\n--- Message {i}: {symbol}/{timeframe} chat={chat_id} rr={rr} ---')
        # mark sent
        try:
            mark_autopost_sent(symbol=symbol, timeframe=timeframe, rr=rr, user_id=str(chat_id))
            print('marked as sent')
        except Exception as e:
            print('mark_autopost_sent failed', e)
        # show candidate passes
        try:
            passes = get_candidate_passes(user_id=str(chat_id), symbol=symbol, timeframe=timeframe)
            print('candidate_passes=', passes)
        except Exception as e:
            print('get_candidate_passes failed', e)
        # attempt to open via bridge
        try:
            tid = handle_autopost_message(m)
            print('handle_autopost_message ->', tid)
        except Exception as e:
            print('handle_autopost_message failed', e)

    # Print DB state
    print_autopost_log(20)
    print_candidates()
    print_open_trades()

if __name__ == '__main__':
    asyncio.get_event_loop().run_until_complete(main())
