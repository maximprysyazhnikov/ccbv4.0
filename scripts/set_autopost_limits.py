#!/usr/bin/env python3
"""Set autopost limit settings (upsert into settings table).

Usage: python scripts/set_autopost_limits.py --run 9999 --day 9999
"""
import argparse
from utils.db import get_conn

parser = argparse.ArgumentParser()
parser.add_argument("--run", type=int, default=9999)
parser.add_argument("--day", type=int, default=9999)
args = parser.parse_args()

with get_conn() as conn:
    cur = conn.cursor()
    cur.execute("INSERT INTO settings(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("max_open_per_run", str(args.run)))
    cur.execute("INSERT INTO settings(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("max_open_per_day", str(args.day)))
    conn.commit()

print(f"Set max_open_per_run={args.run}, max_open_per_day={args.day}")
