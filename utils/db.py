# utils/db.py
from __future__ import annotations
import os
import sqlite3
import contextlib
import logging
import threading
from typing import Iterator, Optional
from queue import Queue, Empty

try:
    from dotenv import load_dotenv

    # Some runtime modules import utils.db before main.py loads .env.
    # Load it here too so DB_PATH is honored during early pool initialization.
    load_dotenv(".env", override=False)
except Exception:
    pass

log = logging.getLogger("db")

# Connection pool
_pool: Optional[Queue[sqlite3.Connection]] = None
_pool_lock = threading.Lock()
_pool_size = int(os.getenv("DB_POOL_SIZE", "5"))

def _mkparent(path: str) -> None:
    parent = os.path.dirname(path) or "."
    try:
        os.makedirs(parent, exist_ok=True)
    except Exception as e:
        log.warning("[db] cannot create dir %s: %s", parent, e)

def _candidates() -> list[str]:
    env_path = os.environ.get("DB_PATH", "").strip()
    cands = []
    if env_path:
        cands.append(env_path)
    # Local storage first (consistent with other modules)
    cands.append(os.path.join(".", "storage", "bot.db"))
    cands.append("/data/bot.db")                 # Railway volume fallback
    cands.append(os.path.join(".", "data", "bot.db"))  # another fallback
    uniq = []
    for p in cands:
        if p not in uniq:
            uniq.append(p)
    return uniq

def _try_connect(path: str) -> Optional[sqlite3.Connection]:
    try:
        _mkparent(path)
        con = sqlite3.connect(path, timeout=30, check_same_thread=False, isolation_level=None)
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA busy_timeout=30000;")
        return con
    except Exception as e:
        log.warning("[db] open failed for %s: %s", path, e)
        return None

def _init_pool() -> None:
    """Initialize connection pool."""
    global _pool
    if _pool is not None:
        return
    
    with _pool_lock:
        if _pool is not None:
            return
        
        _pool = Queue(maxsize=_pool_size)
        db_path = None
        
        # Find working database path
        for path in _candidates():
            con = _try_connect(path)
            if con is not None:
                db_path = path
                con.close()
                break
        
        if db_path is None:
            log.warning("[db] pool initialization: no valid database path found")
            return
        
        # Pre-populate pool
        for _ in range(_pool_size):
            con = _try_connect(db_path)
            if con:
                _pool.put(con)
            else:
                break
        
        log.info("[db] connection pool initialized with %d connections", _pool.qsize())


@contextlib.contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Get database connection from pool."""
    global _pool
    
    # Initialize pool if needed
    if _pool is None:
        _init_pool()
    
    con = None
    from_pool = False
    
    # Try to get from pool first
    if _pool is not None and not _pool.empty():
        try:
            con = _pool.get(timeout=1.0)
            con.execute("SELECT 1").fetchone()
            from_pool = True
        except Exception:
            # Pool connection failed, will fallback
            if con:
                try:
                    con.close()
                except Exception:
                    pass
            con = None
    
    # Fallback to direct connection if pool failed or empty
    if con is None:
        tried = []
        for path in _candidates():
            con = _try_connect(path)
            if con is not None:
                break
            tried.append(path)
        else:
            raise sqlite3.OperationalError(
                "unable to open database file (all candidates failed: " + ", ".join(tried) + ")"
            )
    
    try:
        yield con
        # Commit successful changes
        con.commit()
    except Exception:
        # Rollback on error
        try:
            con.rollback()
        except Exception:
            pass
        raise
    finally:
        if from_pool and _pool is not None:
            # Return to pool
            try:
                _pool.put(con, timeout=1.0)
            except Exception:
                # Connection is bad, close it
                try:
                    con.close()
                except Exception:
                    pass
                # Try to add new connection to pool
                for path in _candidates():
                    new_con = _try_connect(path)
                    if new_con:
                        try:
                            _pool.put(new_con, timeout=0.1)
                        except Exception:
                            new_con.close()
                        break
        else:
            # Direct connection, just close
            try:
                con.close()
            except Exception:
                pass
