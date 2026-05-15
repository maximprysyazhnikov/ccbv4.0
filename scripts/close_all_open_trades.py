#!/usr/bin/env python3
"""Simulate closing all OPEN trades at current market price (best-effort).

- Uses local price providers (services.market, services.binance, etc.) similar to `rr_probe`.
- Calls `close_trade` from `services.trade_engine` for each open trade.
- Prints a summary of closed trades and any skips.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import sqlite3
from importlib import import_module
from services.trade_engine import close_trade

DB = os.getenv("DB_PATH", "storage/bot.db")

PROVIDERS = [
    "services.market",
    "services.price_provider",
    "services.binance_price",
    "services.binance",
    "services.prices",
]


def get_price(symbol: str):
    for modname in PROVIDERS:
        try:
            mod = import_module(modname)
            if hasattr(mod, "get_price"):
                return float(mod.get_price(symbol))
        except Exception:
            continue
    return None


def list_open_trades():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    rows = cur.execute("SELECT id, symbol, timeframe, direction, entry, sl, tp FROM trades WHERE status='OPEN' ORDER BY id").fetchall()
    conn.close()
    return rows


def main():
    rows = list_open_trades()
    if not rows:
        print("No open trades to close")
        return

    closed = []
    skipped = []
    for tid, symbol, timeframe, direction, entry, sl, tp in rows:
        px = get_price(symbol)
        if px is None:
            print(f"No price for {symbol}, closing at entry {entry}")
            px = entry
        print(f"Closing id={tid} {symbol} {timeframe} at price={px}")
        res = close_trade(symbol, timeframe, px, reason="SIMULATED_CLOSE")
        if res:
            closed.append((tid, symbol, px))
        else:
            skipped.append((tid, symbol))

    print("\nSummary:")
    print(f"Closed: {len(closed)}")
    for tid, sym, px in closed:
        print(f"  - id={tid} {sym} @ {px}")
    if skipped:
        print(f"Skipped: {len(skipped)}")
        for tid, sym in skipped:
            print(f"  - id={tid} {sym}")


if __name__ == '__main__':
    main()
