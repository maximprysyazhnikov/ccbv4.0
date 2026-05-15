"""Shared pytest fixtures for CCBV3.8 tests."""
import sqlite3
import pytest
import pandas as pd
import numpy as np
from typing import Generator
from datetime import datetime, timezone


@pytest.fixture
def in_memory_db() -> Generator[sqlite3.Connection, None, None]:
    """Create an in-memory SQLite database for testing."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    
    # Create basic schema
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER DEFAULT 0,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            direction TEXT,
            entry REAL,
            sl REAL,
            tp REAL,
            rr REAL,
            status TEXT,
            ts_created INTEGER
        )
    """)
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            direction TEXT,
            entry REAL,
            sl REAL,
            tp REAL,
            status TEXT,
            size_usd REAL,
            fees_bps INTEGER,
            rr_planned REAL,
            opened_at TEXT
        )
    """)
    
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def sample_ohlcv_data() -> pd.DataFrame:
    """Generate sample OHLCV data for testing."""
    dates = pd.date_range(start="2024-01-01", periods=200, freq="1h")
    np.random.seed(42)
    
    base_price = 100.0
    prices = []
    for i in range(200):
        change = np.random.normal(0, 0.02)
        base_price *= (1 + change)
        prices.append(base_price)
    
    df = pd.DataFrame({
        "ts": [int(d.timestamp()) for d in dates],
        "open": prices,
        "high": [p * (1 + abs(np.random.normal(0, 0.01))) for p in prices],
        "low": [p * (1 - abs(np.random.normal(0, 0.01))) for p in prices],
        "close": prices,
        "volume": [np.random.uniform(1000, 10000) for _ in prices],
    })
    
    return df


@pytest.fixture
def sample_trade_data() -> dict:
    """Sample trade data for testing."""
    return {
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "direction": "LONG",
        "entry": 100.0,
        "sl": 95.0,
        "tp": 115.0,
        "size_usd": 100.0,
        "fees_bps": 10,
    }


@pytest.fixture
def sample_signal_data() -> dict:
    """Sample signal data for testing."""
    return {
        "user_id": 12345,
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "direction": "LONG",
        "entry": 100.0,
        "sl": 95.0,
        "tp": 115.0,
        "rr": 3.0,
        "status": "SUGGESTED",
        "ts_created": int(datetime.now(timezone.utc).timestamp()),
    }


@pytest.fixture
def mock_openrouter_response() -> dict:
    """Mock OpenRouter API response."""
    return {
        "choices": [
            {
                "message": {
                    "content": '{"direction":"LONG","entry":100.0,"stop":95.0,"tp":115.0,"confidence":0.8,"holding_time_hours":24,"rationale":"Test"}'
                }
            }
        ]
    }


@pytest.fixture
def sample_indicators() -> dict:
    """Sample indicator data for gate scoring tests."""
    return {
        "ok": True,
        "ema50": 98.0,
        "ema200": 95.0,
        "atr_pct": 0.005,
        "rsi": 55.0,
        "adx": 20.0,
        "bbw": 0.02,
        "rel_vol": 1.5,
        "vwap_dist": 0.002,
        "ema50_slope": 0.001,
        "price_rel_ema50": 2.0,
        "price_rel_ema200": 5.0,
        "_series": {
            "ema50": np.array([95.0, 96.0, 97.0, 98.0]),
            "ema200": np.array([94.0, 94.5, 95.0, 95.0]),
        },
    }
