import os
import sqlite3
import time
import tempfile
from services import kpi


def make_db(path):
    con = sqlite3.connect(path)
    cur = con.cursor()
    cur.execute(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            symbol TEXT,
            closed_at TEXT,
            status TEXT,
            pnl_usd REAL,
            pnl REAL,
            trade_mode TEXT
        )
        """
    )
    con.commit()
    return con


def test_kpi_separates_scalp_ai_std(tmp_path):
    db = tmp_path / "test_bot.db"
    con = make_db(str(db))
    cur = con.cursor()

    now = int(time.time())
    # Helper to format closed_at as a parseable datetime for SQLite strftime
    def fmt_offset(sec_offset):
        return time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(now + sec_offset))

    rows = [
        # scalping: 3 trades (2 wins, 1 loss)
        (1, 'FOGOUSDT', fmt_offset(-3600), 'CLOSED', 1.0, None, 'scalping'),
        (2, 'FOGOUSDT', fmt_offset(-3500), 'CLOSED', 2.0, None, 'scalping'),
        (3, 'FOGOUSDT', fmt_offset(-3400), 'CLOSED', -1.0, None, 'scalping'),
        # ai: 1 trade (loss)
        (4, 'BTCUSDT', fmt_offset(-3300), 'CLOSED', -1.22, None, 'ai'),
        # standard: 2 trades (losses)
        (5, 'ETHUSDT', fmt_offset(-3200), 'CLOSED', -2.0, None, 'standard'),
        (6, 'ADAUSDT', fmt_offset(-3100), 'CLOSED', -3.44, None, 'standard'),
    ]

    cur.executemany("INSERT INTO trades(id,symbol,closed_at,status,pnl_usd,pnl,trade_mode) VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()

    # Point KPI module to our temp DB
    kpi.DB_PATH = str(db)

    out = kpi.kpi_summary(days=7, table='trades')
    assert '⚡ SCALP' in out
    assert '🤖 AI' in out
    assert '📉 STD' in out
    # check counts
    assert 'SCALP' in out and '3' in out.split('SCALP')[1][:10]
    assert 'AI' in out and '1' in out.split('AI')[1][:10]
    assert 'STD' in out and '2' in out.split('STD')[1][:10]


def test_kpi_ignores_open_trades(tmp_path, monkeypatch):
    """Ensure KPI for last 7d counts only closed trades in winrate and closed counts."""
    import sqlite3, time
    db = tmp_path / "test_bot2.db"
    con = sqlite3.connect(str(db))
    cur = con.cursor()
    cur.execute(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            symbol TEXT,
            opened_at TEXT,
            closed_at TEXT,
            status TEXT,
            pnl_usd REAL,
            rr_realized REAL,
            trade_mode TEXT
        )
        """
    )
    now = int(time.time())
    def fmt(sec):
        return time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(now + sec))

    rows = [
        (1, 'TEST1', fmt(-3600), fmt(-3500), 'CLOSED', 10.0, 2.0, 'scalping'),
        (2, 'TEST1', fmt(-1800), None, 'OPEN', None, None, 'scalping'),
    ]
    cur.executemany("INSERT INTO trades(id,symbol,opened_at,closed_at,status,pnl_usd,rr_realized,trade_mode) VALUES (?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()

    # monkeypatch get_conn to use our DB
    from utils import db as udb
    def _get_conn():
        c = sqlite3.connect(str(db))
        c.row_factory = sqlite3.Row
        return c
    monkeypatch.setattr(udb, 'get_conn', _get_conn)

    from main import _get_scalp_stats
    out = _get_scalp_stats(0, days=7)
    # Should report total 2 trades (including open), but Closed should be 1 and Open 1
    assert 'Trades: 2 (Closed:1 W:1 L:0 Open:1)' in out
    # Winrate should reflect closed-only calculation
    assert 'Winrate (closed only): `100.0%`' in out


def test_kpi_keyboard_active_marker():
    from main import _kpi_keyboard
    kb = _kpi_keyboard('trades', 7)
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert any(lbl == '✅ 7д' for lbl in labels)

