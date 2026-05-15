import pytest
from services.autopost_bridge import _parse


BASIC = """
🤖 Autopost plan BTCUSDT [1h]
Dir: LONG | RR≈2.42
Entry: 45000.0 | SL: 44000.0 | TP: 47000.0
"""

def test_parse_basic_long():
    p = _parse(BASIC)
    assert p is not None
    assert p["symbol"] == "BTCUSDT"
    assert p["timeframe"] == "1h"
    assert p["direction"] == "LONG"
    assert abs(p["rr"] - 2.42) < 0.001

EMOJI_SHORT = """
🤖 Autopost plan ETHUSDT [1h]
🔴 SHORT | RR:1.80
Entry: 2000.0 | SL: 2050.0 | TP: 1900.0
"""

def test_parse_emoji_short():
    p = _parse(EMOJI_SHORT)
    assert p is not None
    assert p["direction"] == "SHORT"
    assert abs(p["rr"] - 1.80) < 0.001

SEPARATE_TOKENS = """
🤖 Autopost plan BNBUSDT [4h]
Direction: BUY
RR=2.00
Entry: 300.0 | SL: 290.0 | TP: 360.0
"""

def test_parse_buy_and_rr_on_separate_line():
    p = _parse(SEPARATE_TOKENS)
    assert p is not None
    assert p["direction"] == "LONG"
    assert abs(p["rr"] - 2.0) < 0.0001

SPACED_NUMBERS = """
🤖 Autopost plan ETHUSDT [5m]
🔴 SHORT | RR:3.50
Entry: 2 692.20 | SL: 2 701.62 | TP: 2 653.16
"""

def test_parse_spaced_numbers():
    p = _parse(SPACED_NUMBERS)
    assert p is not None
    assert p["symbol"] == "ETHUSDT"
    assert p["timeframe"] == "5m"
    assert p["direction"] == "SHORT"
    assert abs(p["rr"] - 3.50) < 0.001
    assert abs(p["entry"] - 2692.20) < 0.0001

SOL_MODE = """
🤖 Autopost plan SOLUSDT [5m]
🔵 LONG | RR:3.50
🔖 Попав у: СКАЛЬП
Entry: 118.46 | SL: 118.05 | TP: 120.18
"""

def test_parse_with_mode():
    p = _parse(SOL_MODE)
    assert p is not None
    assert p["symbol"] == "SOLUSDT"
    assert p.get("trade_mode") == "scalping"

INVALID = """
🤖 Autopost plan AAAA [1h]
Entry: 1.0 | SL: 0.9 | TP: 1.1
"""

def test_parse_missing_dir_rr_returns_none():
    assert _parse(INVALID) is None
