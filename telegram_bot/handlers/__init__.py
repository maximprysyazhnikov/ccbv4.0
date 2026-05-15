"""Telegram bot handlers package."""
from telegram_bot.handlers.register import register_handlers

# Expose the helper `_compute_rr_num` from the top-level handlers module file
# (the project keeps a `handlers.py` module alongside this package).
try:
    import importlib.util
    import os
    handlers_file_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'handlers.py'))
    spec = importlib.util.spec_from_file_location("handlers_file", handlers_file_path)
    _handlers_file = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(_handlers_file)
    _compute_rr_num = getattr(_handlers_file, '_compute_rr_num')
except Exception:
    try:
        from services.autopost import _compute_rr_num as _compute_rr_num
    except Exception:
        # Fallback implementation to avoid import-time dependencies in tests
        def _compute_rr_num(direction: str, entry: float, stop: float, tp: float):
            try:
                import math
                if any(math.isnan(x) for x in [entry, stop, tp]):
                    return None
                if direction == "LONG":
                    risk = entry - stop; reward = tp - entry
                elif direction == "SHORT":
                    risk = stop - entry; reward = entry - tp
                else:
                    return None
                if risk <= 0 or reward <= 0:
                    return None
                return float(reward / risk)
            except Exception:
                return None
        # expose fallback
        _compute_rr_num = _compute_rr_num

__all__ = ["register_handlers", "_compute_rr_num"]
