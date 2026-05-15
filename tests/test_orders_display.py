import sqlite3
import time
from utils import db as udb


def _make_db(path):
    con = sqlite3.connect(path)
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
    con.commit()
    con.close()


def test_orders_hides_only_test_symbols(tmp_path, monkeypatch):
    db = tmp_path / "orders1.db"
    _make_db(str(db))
    con = sqlite3.connect(str(db))
    cur = con.cursor()
    # Only test symbol (TPAIR) present
    cur.execute("INSERT INTO trades(symbol,timeframe,direction,entry,sl,tp,opened_at,status,trade_mode) VALUES (?,?,?,?,?,?,?,?,?)",
                ('TPAIR','5m','LONG',1.0,0.9,1.1,'now','OPEN','ai'))
    con.commit()
    con.close()

    def _get_conn():
        c = sqlite3.connect(str(db))
        c.row_factory = sqlite3.Row
        return c
    monkeypatch.setattr(udb, 'get_conn', _get_conn)

    from main import _get_open_orders
    txt = _get_open_orders(None)
    assert 'Немає відкритих ордерів' in txt


def test_orders_excludes_test_symbol_and_shows_real(tmp_path, monkeypatch):
    db = tmp_path / "orders2.db"
    _make_db(str(db))
    con = sqlite3.connect(str(db))
    cur = con.cursor()
    # Insert a test symbol and a real symbol
    cur.execute("INSERT INTO trades(symbol,timeframe,direction,entry,sl,tp,opened_at,status,trade_mode) VALUES (?,?,?,?,?,?,?,?,?)",
                ('TPAIR','5m','LONG',1.0,0.9,1.1,'now','OPEN','ai'))
    cur.execute("INSERT INTO trades(symbol,timeframe,direction,entry,sl,tp,opened_at,status,trade_mode) VALUES (?,?,?,?,?,?,?,?,?)",
                ('BTCUSDT','5m','SHORT',10.0,10.5,9.5,'now','OPEN','scalping'))
    con.commit()
    con.close()

    def _get_conn():
        c = sqlite3.connect(str(db))
        c.row_factory = sqlite3.Row
        return c
    monkeypatch.setattr(udb, 'get_conn', _get_conn)

    from main import _get_open_orders
    txt = _get_open_orders(None)
    assert 'BTCUSDT' in txt
    assert 'TPAIR' not in txt
