# utils/symbol_validator.py
"""
Валідація торгових символів через Binance API.
"""
from __future__ import annotations
import json
import logging
from typing import List, Tuple, Set
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

log = logging.getLogger(__name__)

BINANCE_EXCHANGE_INFO = "https://api.binance.com/api/v3/exchangeInfo"

# Cache for valid symbols
_valid_symbols_cache: Set[str] = set()
_cache_loaded: bool = False


def _load_valid_symbols() -> Set[str]:
    """Load all valid USDT trading pairs from Binance."""
    global _valid_symbols_cache, _cache_loaded
    
    if _cache_loaded and _valid_symbols_cache:
        return _valid_symbols_cache
    
    try:
        req = Request(BINANCE_EXCHANGE_INFO, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        
        symbols = set()
        for s in data.get("symbols", []):
            # Only active USDT pairs
            if s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT":
                symbols.add(s["symbol"])
        
        _valid_symbols_cache = symbols
        _cache_loaded = True
        log.info(f"[validator] Loaded {len(symbols)} valid USDT symbols from Binance")
        return symbols
    except (URLError, HTTPError, json.JSONDecodeError) as e:
        log.warning(f"[validator] Failed to load symbols from Binance: {e}")
        # Return some defaults if API fails
        return {
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
            "ADAUSDT", "DOGEUSDT", "DOTUSDT", "LTCUSDT", "LINKUSDT",
            "AVAXUSDT", "MATICUSDT", "UNIUSDT", "XLMUSDT", "ATOMUSDT",
        }


def validate_symbols(symbols_input: str) -> Tuple[List[str], List[str]]:
    """
    Validate symbols from user input.
    
    Args:
        symbols_input: Comma-separated symbols (e.g., "BTCUSDT, ETHUSDT, BNB")
    
    Returns:
        Tuple of (valid_symbols, invalid_symbols)
    """
    valid_binance = _load_valid_symbols()
    
    # Parse input
    raw_symbols = [s.strip().upper() for s in symbols_input.split(",") if s.strip()]
    
    valid: List[str] = []
    invalid: List[str] = []
    
    for sym in raw_symbols:
        # Normalize: add USDT if not present
        normalized = sym if sym.endswith("USDT") else f"{sym}USDT"
        
        if normalized in valid_binance:
            if normalized not in valid:  # Avoid duplicates
                valid.append(normalized)
        else:
            invalid.append(sym)
    
    return valid, invalid


def format_symbols_for_display(symbols: List[str], max_display: int = 8) -> str:
    """Format symbols list for display in Telegram."""
    if not symbols:
        return "—"
    
    if len(symbols) <= max_display:
        return ", ".join(symbols)
    
    shown = symbols[:max_display]
    hidden = len(symbols) - max_display
    return f"{', '.join(shown)} (+{hidden})"


def is_valid_symbol(symbol: str) -> bool:
    """Check if a single symbol is valid."""
    valid_binance = _load_valid_symbols()
    normalized = symbol.upper()
    if not normalized.endswith("USDT"):
        normalized = f"{normalized}USDT"
    return normalized in valid_binance
