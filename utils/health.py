"""Health check utilities."""
from __future__ import annotations

import logging
import sqlite3
from typing import Dict, Any, Optional

from utils.db import get_conn

log = logging.getLogger("health")


def check_database() -> Dict[str, Any]:
    """Check database connectivity."""
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1").fetchone()
        return {"status": "healthy", "error": None}
    except Exception as e:
        log.error("Database health check failed: %s", e)
        return {"status": "unhealthy", "error": str(e)}


def check_binance() -> Dict[str, Any]:
    """Check Binance API connectivity."""
    try:
        import requests
        from core_config import CFG
        
        # Simple ping to Binance API
        response = requests.get(
            "https://api.binance.com/api/v3/ping",
            timeout=5
        )
        response.raise_for_status()
        return {"status": "healthy", "error": None}
    except Exception as e:
        log.error("Binance health check failed: %s", e)
        return {"status": "unhealthy", "error": str(e)}


def check_openrouter() -> Dict[str, Any]:
    """Check OpenRouter API availability."""
    try:
        from core_config import CFG
        
        if not CFG.get("or_slots"):
            return {"status": "not_configured", "error": "No OpenRouter slots configured"}
        
        # Just check if slots are available, don't make actual API call
        return {"status": "healthy", "error": None}
    except Exception as e:
        log.error("OpenRouter health check failed: %s", e)
        return {"status": "unhealthy", "error": str(e)}


def health_status() -> Dict[str, Any]:
    """Get overall health status."""
    db_health = check_database()
    binance_health = check_binance()
    openrouter_health = check_openrouter()
    
    overall_healthy = all(
        h["status"] in ("healthy", "not_configured")
        for h in [db_health, binance_health, openrouter_health]
    )
    
    return {
        "status": "healthy" if overall_healthy else "degraded",
        "components": {
            "database": db_health,
            "binance": binance_health,
            "openrouter": openrouter_health,
        }
    }
