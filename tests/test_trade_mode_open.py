import sqlite3
from services import trade_engine
import time
import pytest


def _make_db(path):
    con = sqlite3.connect(path)
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE trades (
        id INTEGER PRIMARY KEY,
        signal_id TEXT,
        symbol TEXT,
        timeframe TEXT,
        direction TEXT,
        entry REAL,
        sl REAL,
        tp REAL,
        opened_at TEXT,
        size_usd REAL,
        fees_bps REAL,
        rr_planned REAL,
        status TEXT,
        trade_mode TEXT,
        ema50 REAL,
        ema200 REAL,
        atr_entry REAL,
        rr_target REAL
    )
    """)
    con.commit()
    con.close()


def test_open_trade_allows_different_trade_modes(tmp_path, monkeypatch):
    db = tmp_path / "test_bot.db"
    _make_db(str(db))

    # Point service to our DB
    monkeypatch.setattr(trade_engine, "_get_db_conn", None)
    trade_engine.DB_PATH = str(db)

    sig = {
        'id': 's1',
        'symbol': 'TPAIR',
        'timeframe': '5m',
        'direction': 'LONG',
        'entry': 1.0,
        'sl': 0.9,
        'tp': 1.1,
        'ind': {},
        'rr': 2.0,
    }

    # Open standard trade
    tid1 = trade_engine.open_trade_from_signal(dict(sig), trade_mode='standard')
    assert tid1 is not None

    # Opening the same trade_mode should be idempotent (return None)
    tid1b = trade_engine.open_trade_from_signal(dict(sig), trade_mode='standard')
    assert tid1b is None

    # Opening a different trade_mode should succeed
    tid2 = trade_engine.open_trade_from_signal(dict(sig), trade_mode='ai')
    assert tid2 is not None

    # Verify two OPEN rows with different trade_mode
    with trade_engine._connect() as conn:
        rows = conn.execute("SELECT trade_mode FROM trades WHERE symbol=? AND timeframe=? AND status='OPEN'", ('TPAIR', '5m')).fetchall()
        modes = sorted([r[0] for r in rows])
        assert modes == ['ai', 'standard']


def test_find_open_trades_respects_trade_mode(tmp_path):
    db = tmp_path / "test2.db"
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE trades (
        id INTEGER PRIMARY KEY,
        symbol TEXT,
        timeframe TEXT,
        direction TEXT,
        entry REAL,
        sl REAL,
        tp REAL,
        opened_at TEXT,
        status TEXT,
        trade_mode TEXT
    )
    """)
    cur.execute("INSERT INTO trades(symbol,timeframe,direction,entry,sl,tp,opened_at,status,trade_mode) VALUES (?,?,?,?,?,?,?,?,?)",
                ('TPAIR', '5m', 'LONG', 1.0, 0.9, 1.1, 'now', 'OPEN', 'ai'))
    con.commit()

    # Use the cursor directly with autopost_bridge helper
    from services import autopost_bridge
    rows_all = autopost_bridge._find_open_trades(cur, 'TPAIR', '5m')
    rows_ai = autopost_bridge._find_open_trades(cur, 'TPAIR', '5m', trade_mode='ai')
    rows_std = autopost_bridge._find_open_trades(cur, 'TPAIR', '5m', trade_mode='standard')

    assert len(rows_all) == 1
    assert len(rows_ai) == 1
    assert len(rows_std) == 0

    con.close()
