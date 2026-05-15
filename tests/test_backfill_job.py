import main
import pytest

def test_backfill_job_scheduled():
    app = main.build_app()
    jobs = [job.name for job in app.job_queue.jobs()]
    assert any('backfill_signals' in j for j in jobs), 'Backfill job not scheduled'

def test_backfill_idempotent(tmp_path):
    # Simulate backfill_signals inserting signals, then re-run and check no duplicates
    from tools.backfill_signals_from_trades import backfill_signals
    import sqlite3, time
    db = tmp_path / 'test_bot.db'
    con = sqlite3.connect(str(db))
    con.execute('CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT, closed_at TEXT, status TEXT, pnl_usd REAL, trade_mode TEXT)')
    now = int(time.time())
    rows = [
        (1, 'SOLUSDT', str(now-1000), 'CLOSED', -52.91, 'scalping'),
        (2, 'BTCUSDT', str(now-900), 'CLOSED', 1.23, 'ai'),
    ]
    con.executemany('INSERT INTO trades(id,symbol,closed_at,status,pnl_usd,trade_mode) VALUES (?,?,?,?,?,?)', rows)
    con.commit()
    con.close()
    # Patch DB_PATH for backfill
    import tools.backfill_signals_from_trades as bfs
    bfs.DB_PATH = str(db)
    # First run
    inserted1 = backfill_signals(7, dry=False)
    # Second run (should insert 0)
    inserted2 = backfill_signals(7, dry=False)
    assert inserted2 == 0, 'Backfill is not idempotent'
