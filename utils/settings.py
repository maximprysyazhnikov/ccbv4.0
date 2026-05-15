from __future__ import annotations
import os, sqlite3, time
from typing import Optional, Tuple, Dict
from utils.db import get_conn

_CACHE: Dict[str, Tuple[str, float]] = {}
_TTL = 10.0  # сек

def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    now = time.time()
    v = _CACHE.get(key)
    if v and now - v[1] < _TTL:
        return v[0]
    with get_conn() as conn:
        cur = conn.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = cur.fetchone()
        if row and row[0] is not None:
            _CACHE[key] = (str(row[0]), now)
            return str(row[0])
    env = os.getenv(key.upper()) or os.getenv(key.lower())
    if env is not None:
        _CACHE[key] = (env, now)
        return env
    return default

def get_setting_float(key: str, default: float = 0.0) -> float:
    """Get setting as float"""
    val = get_setting(key)
    if val is not None:
        try:
            return float(val)
        except (ValueError, TypeError):
            pass
    return default

def get_setting_int(key: str, default: int = 0) -> int:
    """Get setting as int"""
    val = get_setting(key)
    if val is not None:
        try:
            return int(val)
        except (ValueError, TypeError):
            pass
    return default
