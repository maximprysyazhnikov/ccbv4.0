import sqlite3
import time
import tempfile
import os

import pytest
import main
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
            trade_mode TEXT
        )
        """
    )
    con.commit()
    return con


class DummyMessage:
    def __init__(self, text=None):
        self.text = text
        self.sent = []

    async def reply_text(self, text, **kwargs):
        self.sent.append((text, kwargs))


class DummyCQ:
    def __init__(self, data, message=None):
        self.data = data
        self.message = message or DummyMessage()
        self.from_user = type("U", (), {"id": 123})()
        self.answered = False

    async def answer(self, *args, **kwargs):
        self.answered = True


class DummyUpdate:
    def __init__(self, cq):
        self.callback_query = cq


def test_kpi_keyboard_has_breakdown_button():
    kb = main._kpi_keyboard('trades', 7)
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert any('Show breakdown' in lbl for lbl in labels)


def test_kpi_break_callback_shows_breakdown(tmp_path, monkeypatch):
    import asyncio
    db = tmp_path / "test_trades.db"
    con = make_db(str(db))
    cur = con.cursor()

    now = int(time.time())
    rows = [
        (1, 'SOLUSDT', str(now-1000), 'CLOSED', -52.91, 'scalping'),
        (2, 'SOLUSDT', str(now-900), 'CLOSED', -2.82, 'standard'),
        (3, 'BTCUSDT', str(now-800), 'CLOSED', -1.22, 'ai'),
        (4, 'BTCUSDT', str(now-700), 'CLOSED', 1.23, 'scalping'),
    ]
    cur.executemany("INSERT INTO trades(id,symbol,closed_at,status,pnl_usd,trade_mode) VALUES (?,?,?,?,?,?)", rows)
    con.commit(); con.close()

    kpi.DB_PATH = str(db)
    from utils import db as udb
    def _get_conn():
        c = sqlite3.connect(str(db))
        c.row_factory = sqlite3.Row
        return c
    monkeypatch.setattr(udb, 'get_conn', _get_conn)

    msg = DummyMessage()
    cq = DummyCQ(data='kpi:break:trades:3', message=msg)
    upd = DummyUpdate(cq)

    asyncio.run(main.kpi_break_cb(upd, None))

    assert msg.sent, "No breakdown message sent"
    text, _ = msg.sent[-1]
    assert 'SOLUSDT' in text
    assert ('scal' in text) or ('scalp' in text) or ('scalping' in text)
    assert ('stan' in text) or ('standard' in text)
    assert 'BTCUSDT' in text
