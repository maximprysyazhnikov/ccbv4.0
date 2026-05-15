#!/usr/bin/env python3
import sqlite3
from datetime import datetime, timezone, timedelta

DB='storage/bot.db'
DAYS=7

def fmt(x):
    return f"{x:+.2f}" if isinstance(x, float) else str(x)

with sqlite3.connect(DB) as conn:
    cur=conn.cursor()
    t_start = (datetime.utcnow() - timedelta(days=DAYS)).isoformat()

    q = """
    SELECT trade_mode, status, symbol, COUNT(*) as n, SUM(COALESCE(pnl_usd,0)) as pnl_usd
    FROM trades WHERE opened_at >= ? GROUP BY trade_mode, status, symbol
    ORDER BY trade_mode, symbol
    """
    rows=cur.execute(q,(t_start,)).fetchall()

    # Aggregate
    modes = {}
    for mode, status, symbol, n, pnl in rows:
        mode = mode or 'standard'
        modes.setdefault(mode, {})
        sym = modes[mode].setdefault(symbol, {'W':0,'L':0,'O':0,'n':0,'pnl':0.0})
        sym['n'] += n
        sym['pnl'] += pnl
        if status == 'WIN':
            sym['W'] += n
        elif status == 'LOSS':
            sym['L'] += n
        elif status == 'OPEN':
            sym['O'] += n

    for mode, syms in modes.items():
        total = sum(s['n'] for s in syms.values())
        wins = sum(s['W'] for s in syms.values())
        losses = sum(s['L'] for s in syms.values())
        open_ = sum(s['O'] for s in syms.values())
        winrate = (wins/(wins+losses)*100) if (wins+losses)>0 else 0
        pnl = sum(s['pnl'] for s in syms.values())
        print(f"\nMODE: {mode.upper()} — Trades: {total} (W:{wins} L:{losses} O:{open_}) — WR: {winrate:.1f}% — PnL: {pnl:+.2f}")
        print("By symbol:")
        for sym,(v) in syms.items():
            w=v['W']; l=v['L']; p=v['pnl']
            print(f"  {sym}: {w}W/{l}L ({(w/(w+l)*100) if (w+l)>0 else 0:.1f}%) -> {p:+.2f}")

    if not modes:
        print('No trades in last', DAYS, 'days')
