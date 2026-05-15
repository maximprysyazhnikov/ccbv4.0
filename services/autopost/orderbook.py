"""Order book helper functions."""
from __future__ import annotations

from typing import Any, Dict, Optional

from core_config import CFG
from utils.settings_ob import is_ob_enabled
from market_data.orderbook_light import get_orderbook_metrics

try:
    from market_data.orderbook import get_orderbook
except Exception:
    get_orderbook = None


def get_wall_info(symbol: str, entry: float, direction: str) -> Optional[Dict[str, Any]]:
    """Get wall information from orderbook."""
    if not is_ob_enabled():
        return None
    
    try:
        ob = get_orderbook(symbol, limit=1000) if get_orderbook else None
        if not ob:
            return None
        
        metrics = get_orderbook_metrics(ob, entry_price=entry)
        if not metrics:
            return None
        
        wall_threshold = float(CFG.get("wall_usdt_threshold", 2000000))
        wall_near_pct = float(CFG.get("wall_near_pct", 1.0))
        
        support_wall = None
        resistance_wall = None
        
        if direction == "LONG":
            support_wall = metrics.get("support_wall")
            resistance_wall = metrics.get("resistance_wall")
        else:
            support_wall = metrics.get("resistance_wall")
            resistance_wall = metrics.get("support_wall")
        
        result = {}
        if support_wall and support_wall.get("volume_usdt", 0) >= wall_threshold:
            dist_pct = abs(support_wall["price"] - entry) / entry * 100.0
            if dist_pct <= wall_near_pct:
                result["support_wall"] = {
                    "price": support_wall["price"],
                    "vol_str": f"{support_wall['volume_usdt']/1e6:.2f}M",
                    "dist_str": f"{dist_pct:.2f}%",
                }
        
        if resistance_wall and resistance_wall.get("volume_usdt", 0) >= wall_threshold:
            dist_pct = abs(resistance_wall["price"] - entry) / entry * 100.0
            if dist_pct <= wall_near_pct:
                result["resistance_wall"] = {
                    "price": resistance_wall["price"],
                    "vol_str": f"{resistance_wall['volume_usdt']/1e6:.2f}M",
                    "dist_str": f"{dist_pct:.2f}%",
                }
        
        imbalance = metrics.get("imbalance")
        if imbalance is not None:
            result["imbalance"] = imbalance
        
        return result if result else None
    
    except Exception:
        return None
