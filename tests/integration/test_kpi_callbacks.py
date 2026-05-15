import time
import sqlite3
import tempfile
import pytest
from types import SimpleNamespace

from services import kpi
import main


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


class DummyMessage:
    def __init__(self, text=None, reply_markup=None):
        self.text = text
        self.reply_markup = reply_markup


class DummyCallbackQuery:
    def __init__(self, data, message: DummyMessage = None):
        self.data = data
        self.message = message or DummyMessage()
        self.from_user = SimpleNamespace(id=123)
        self.answered = []
        self.edited_text = None
        self.edited_markup = None

    async def answer(self, *args, **kwargs):
        # capture args for assertions
        self.answered.append((args, kwargs))

    async def edit_message_text(self, text=None, reply_markup=None, **kwargs):
        self.edited_text = text
        self.edited_markup = reply_markup


class DummyUpdate:
    def __init__(self, cq: DummyCallbackQuery):
        self.callback_query = cq


def test_kpi_callback_edits_message(tmp_path):
    # prepare DB
    db = tmp_path / "test_bot.db"
    con = make_db(str(db))
    cur = con.cursor()

    now = int(time.time())
    fmt = lambda offset: time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(now + offset))

    rows = [
        (1, 'FOGOUSDT', fmt(-3600), 'CLOSED', 1.0, None, 'scalping'),
        (2, 'BTCUSDT', fmt(-3500), 'CLOSED', -1.22, None, 'ai'),
        (3, 'ETHUSDT', fmt(-3400), 'CLOSED', -2.0, None, 'standard'),
    ]
    cur.executemany("INSERT INTO trades(id,symbol,closed_at,status,pnl_usd,pnl,trade_mode) VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()

    kpi.DB_PATH = str(db)

    # Build expected markup
    expected_markup = main._kpi_keyboard('trades', 7)

    # Dummy message that would be present in the chat
    msg = DummyMessage(text="old", reply_markup=None)
    cq = DummyCallbackQuery(data="kpi:trades:7", message=msg)
    upd = DummyUpdate(cq)

    import asyncio
    asyncio.run(main.kpi_cb(upd, None))

    assert cq.edited_text is not None
    assert 'SCALP' in cq.edited_text or 'SCALPING' in cq.edited_text
    # markup equals expected
    assert cq.edited_markup is not None
    assert cq.edited_markup.to_dict() == expected_markup.to_dict()


def test_kpi_callback_close(tmp_path):
    # close action should edit message to closed notice
    cq = DummyCallbackQuery(data="kpi:close", message=DummyMessage(text="irrelevant"))
    upd = DummyUpdate(cq)

    import asyncio
    asyncio.run(main.kpi_cb(upd, None))

    assert cq.edited_text == "KPI view closed."


def test_kpi_callback_already_updated(tmp_path):
    # prepare DB and expected current text
    db = tmp_path / "test_bot2.db"
    con = make_db(str(db))
    cur = con.cursor()

    now = int(time.time())
    fmt = lambda offset: time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(now + offset))

    rows = [
        (1, 'FOGOUSDT', fmt(-3600), 'CLOSED', 1.0, None, 'scalping'),
    ]
    cur.executemany("INSERT INTO trades(id,symbol,closed_at,status,pnl_usd,pnl,trade_mode) VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()

    kpi.DB_PATH = str(db)

    text = kpi.kpi_summary(days=7, table='trades')
    text = main._append_pnl_bars(text)
    markup = main._kpi_keyboard('trades', 7)

    msg = DummyMessage(text=text, reply_markup=markup)
    cq = DummyCallbackQuery(data="kpi:trades:7", message=msg)
    upd = DummyUpdate(cq)

    import asyncio
    asyncio.run(main.kpi_cb(upd, None))

    # Should've answered with "Вже оновлено ✅" and not edited message
    assert cq.answered, "CallbackQuery.answer was not called"
    args, kwargs = cq.answered[-1]
    assert args and args[0] == "Вже оновлено ✅"
    assert kwargs.get("show_alert") is False
    assert cq.edited_text is None