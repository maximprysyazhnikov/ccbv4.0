import pytest

from services.position_manager import _close_position_with_pnl


def test_partial_close_books_pnl_and_reduces_remaining_size(in_memory_db):
    conn = in_memory_db
    conn.execute("ALTER TABLE trades ADD COLUMN partial_50_done INTEGER NOT NULL DEFAULT 0")
    conn.execute("ALTER TABLE trades ADD COLUMN close_price REAL")
    conn.execute("ALTER TABLE trades ADD COLUMN close_reason TEXT")
    conn.execute("ALTER TABLE trades ADD COLUMN rr_realized REAL")
    conn.execute("ALTER TABLE trades ADD COLUMN pnl_usd REAL DEFAULT 0.0")
    conn.execute("ALTER TABLE trades ADD COLUMN closed_at INTEGER")
    conn.execute(
        """
        INSERT INTO trades(id, symbol, timeframe, direction, entry, sl, tp, status, size_usd, pnl_usd)
        VALUES(1, 'BTCUSDT', '5m', 'LONG', 100.0, 95.0, 115.0, 'OPEN', 100.0, 0.0)
        """
    )

    _close_position_with_pnl(
        conn,
        tid=1,
        symbol="BTCUSDT",
        direction="LONG",
        entry=100.0,
        sl=95.0,
        close_price=105.0,
        reason="partial_tp",
        partial_pct=0.5,
    )

    row = conn.execute(
        "SELECT status, size_usd, partial_50_done, pnl_usd, rr_realized FROM trades WHERE id=1"
    ).fetchone()

    assert row["status"] == "OPEN"
    assert row["size_usd"] == pytest.approx(50.0)
    assert row["partial_50_done"] == 1
    assert row["pnl_usd"] == pytest.approx(2.45)
    assert row["rr_realized"] == pytest.approx(1.0)


def test_full_close_adds_remaining_pnl_after_partial(in_memory_db):
    conn = in_memory_db
    conn.execute("ALTER TABLE trades ADD COLUMN partial_50_done INTEGER NOT NULL DEFAULT 0")
    conn.execute("ALTER TABLE trades ADD COLUMN close_price REAL")
    conn.execute("ALTER TABLE trades ADD COLUMN close_reason TEXT")
    conn.execute("ALTER TABLE trades ADD COLUMN rr_realized REAL")
    conn.execute("ALTER TABLE trades ADD COLUMN pnl_usd REAL DEFAULT 0.0")
    conn.execute("ALTER TABLE trades ADD COLUMN closed_at INTEGER")
    conn.execute(
        """
        INSERT INTO trades(id, symbol, timeframe, direction, entry, sl, tp, status, size_usd, pnl_usd)
        VALUES(1, 'BTCUSDT', '5m', 'LONG', 100.0, 95.0, 115.0, 'OPEN', 50.0, 2.45)
        """
    )

    _close_position_with_pnl(
        conn,
        tid=1,
        symbol="BTCUSDT",
        direction="LONG",
        entry=100.0,
        sl=95.0,
        close_price=115.0,
        reason="TP",
        partial_pct=1.0,
    )

    row = conn.execute(
        "SELECT status, close_reason, pnl_usd, rr_realized FROM trades WHERE id=1"
    ).fetchone()

    assert row["status"] == "CLOSED"
    assert row["close_reason"] == "TP"
    assert row["pnl_usd"] == pytest.approx(9.9)
    assert row["rr_realized"] == pytest.approx(3.0)
