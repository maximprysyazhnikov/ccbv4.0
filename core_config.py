from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from dotenv import load_dotenv

# Keep the legacy `env` file as a fallback, but prefer the active `.env`.
load_dotenv("env")
load_dotenv(".env", override=True)

try:
    from config.trading_defaults import apply_trading_defaults
    apply_trading_defaults()
except Exception:
    pass

# Import Pydantic settings
try:
    from config.settings import settings as _pydantic_settings
    _USE_PYDANTIC = True
except ImportError:
    _USE_PYDANTIC = False
    _pydantic_settings = None


def _parse_or_slots_from_env() -> List[Dict[str, Any]]:
    """
    Підтримує три формати:
    1) OR_SLOTS як JSON:
       OR_SLOTS=[{"key":"your_openrouter_key","model":"deepseek/deepseek-chat","base":"...","timeout":20}, ...]
    2) OR_SLOTS як простий список:
       OR_SLOTS=your_openrouter_key_1:deepseek/deepseek-chat,your_openrouter_key_2:openai/gpt-4o-mini
    3) Пара змінних через кому:
       OPENROUTER_KEYS=your_openrouter_key_1,your_openrouter_key_2
       OPENROUTER_MODEL=deepseek/deepseek-chat,openai/gpt-4o-mini
       (якщо моделей менше — копіюємо першу на всі ключі)
    """
    slots: List[Dict[str, Any]] = []

    raw = (os.getenv("OR_SLOTS", "") or "").strip()
    if raw:
        # JSON?
        if raw.startswith("["):
            try:
                data = json.loads(raw)
                for s in data:
                    key = s.get("key") or s.get("api_key")
                    model = s.get("model")
                    base = s.get("base")
                    timeout = s.get("timeout")
                    if key and model:
                        slots.append(
                            {
                                "key": key,
                                "model": model,
                                "base": base,
                                "timeout": timeout,
                            }
                        )
            except Exception:
                # Якщо формат зламаний — мовчки ігноруємо та підемо в інші гілки
                pass
        else:
            # simple form: key:model,key2:model2
            parts = [p.strip() for p in raw.split(",") if p.strip()]
            for p in parts:
                if ":" in p:
                    k, m = [x.strip() for x in p.split(":", 1)]
                    if k and m:
                        slots.append({"key": k, "model": m})
    # доповнюємо з OPENROUTER_KEYS / OPENROUTER_MODEL, якщо треба
    if not slots:
        keys = [
            k.strip()
            for k in (os.getenv("OPENROUTER_KEYS", "") or "").split(",")
            if k.strip()
        ]
        models = [
            m.strip()
            for m in (os.getenv("OPENROUTER_MODEL", "") or "").split(",")
            if m.strip()
        ]
        if keys and models:
            if len(models) < len(keys):
                models += [models[0]] * (len(keys) - len(models))
            for k, m in zip(keys, models):
                slots.append({"key": k, "model": m})

    # фільтр і підстановка дефолтів base/timeout
    base_default = os.getenv("OPENROUTER_BASE", "https://openrouter.ai/api/v1")
    timeout_default = int(os.getenv("OPENROUTER_TIMEOUT", "30") or 30)
    normalized: List[Dict[str, Any]] = []
    for s in slots:
        if not (s.get("key") and s.get("model")):
            continue
        normalized.append(
            {
                "key": s["key"],
                "model": s["model"],
                "base": s.get("base") or base_default,
                "timeout": int(s.get("timeout") or timeout_default),
            }
        )
    return normalized


class _CFGDict(dict):
    """Backward-compatible dict that delegates to Pydantic settings."""
    
    def __init__(self):
        super().__init__()
        self._pydantic = _pydantic_settings if _USE_PYDANTIC else None
    
    def __getitem__(self, key: str) -> Any:
        if self._pydantic:
            # Map keys to Pydantic settings
            key_map = {
                "tg_token": lambda: self._pydantic.tg_token,
                "tg_chat_id": lambda: self._pydantic.tg_chat_id,
                "bot_mode": lambda: self._pydantic.bot_mode,
                "webhook_url": lambda: self._pydantic.webhook_url,
                "port": lambda: self._pydantic.port,
                "tz": lambda: self._pydantic.tz,
                "default_locale": lambda: self._pydantic.default_locale,
                "symbols": lambda: self._pydantic.symbols,
                "metals_symbols": lambda: self._pydantic.metals_symbols,
                "analyze_timeframe": lambda: self._pydantic.analyze_timeframe,
                "analyze_limit": lambda: self._pydantic.analyze_limit,
                "or_slots": lambda: self._pydantic.or_slots,
                "or_base": lambda: self._pydantic.or_base,
                "or_timeout": lambda: self._pydantic.or_timeout,
                "autopost_interval_sec": lambda: self._pydantic.autopost_interval_sec,
                "autopost_cooldown_min": lambda: self._pydantic.autopost_cooldown_min,
                "orderbook_enabled": lambda: self._pydantic.orderbook_enabled,
                "orderbook_levels": lambda: self._pydantic.orderbook_levels,
                "orderbook_ttl_sec": lambda: self._pydantic.orderbook_ttl_sec,
                "orderbook_bucket_pct": lambda: self._pydantic.orderbook_bucket_pct,
                "wall_usdt_threshold": lambda: self._pydantic.wall_usdt_threshold,
                "wall_near_pct": lambda: self._pydantic.wall_near_pct,
            }
            if key in key_map:
                return key_map[key]()
            # Fallback for keys not in map
            if key == "autopost_cron":
                return os.getenv("AUTOPOST_SCAN_CRON", "")
            if key == "ai_force_llm":
                return os.getenv("AI_FORCE_LLM", "true").lower() in ("1", "true", "yes", "on")
        
        # Fallback to original logic if Pydantic not available
        return self._get_fallback(key)
    
    def _get_fallback(self, key: str) -> Any:
        """Fallback to original env-based logic."""
        fallback_map = {
            "tg_token": lambda: os.getenv("TELEGRAM_BOT_TOKEN"),
            "tg_chat_id": lambda: os.getenv("TELEGRAM_CHAT_ID"),
            "bot_mode": lambda: os.getenv("BOT_MODE", "polling"),
            "webhook_url": lambda: os.getenv("WEBHOOK_URL"),
            "port": lambda: int(os.getenv("PORT", "8080") or 8080),
            "tz": lambda: os.getenv("TZ_NAME", "Europe/Kyiv"),
            "default_locale": lambda: os.getenv("DEFAULT_LOCALE", "uk"),
            "symbols": lambda: [
                s.strip()
                for s in (os.getenv("MONITORED_SYMBOLS", "BTCUSDT") or "").split(",")
                if s.strip()
            ],
            "metals_symbols": lambda: [
                s.strip()
                for s in (os.getenv("METALS_SYMBOLS", "XAUUSD,XAGUSD") or "").split(",")
                if s.strip()
            ],
            "analyze_timeframe": lambda: os.getenv("ANALYZE_TIMEFRAME", "15m"),
            "analyze_limit": lambda: int(os.getenv("ANALYZE_LIMIT", "150") or 150),
            "or_slots": lambda: _parse_or_slots_from_env(),
            "or_base": lambda: os.getenv("OPENROUTER_BASE", "https://openrouter.ai/api/v1"),
            "or_timeout": lambda: int(os.getenv("OPENROUTER_TIMEOUT", "30") or 30),
            "autopost_cron": lambda: os.getenv("AUTOPOST_SCAN_CRON", ""),
            "autopost_interval_sec": lambda: int(os.getenv("AUTOPOST_INTERVAL_SEC", "300") or 300),
            "autopost_cooldown_min": lambda: int(os.getenv("AUTOPOST_COOLDOWN_MIN", "30") or 30),
            "ai_force_llm": lambda: os.getenv("AI_FORCE_LLM", "true").lower() in ("1", "true", "yes", "on"),
            "orderbook_enabled": lambda: os.getenv("ORDERBOOK_ENABLED", "true").lower() in ("1", "true", "yes", "on"),
            "orderbook_levels": lambda: int(os.getenv("ORDERBOOK_LEVELS", "50")),
            "orderbook_ttl_sec": lambda: int(os.getenv("ORDERBOOK_TTL_SEC", "20")),
            "orderbook_bucket_pct": lambda: float(os.getenv("ORDERBOOK_BUCKET_PCT", "0.10")),
            "wall_usdt_threshold": lambda: float(os.getenv("WALL_USDT_THRESHOLD", "2000000")),
            "wall_near_pct": lambda: float(os.getenv("WALL_NEAR_PCT", "1.0")),
        }
        if key in fallback_map:
            return fallback_map[key]()
        raise KeyError(key)
    
    def get(self, key: str, default: Any = None) -> Any:
        """Dict.get() compatibility."""
        try:
            return self[key]
        except KeyError:
            return default
    
    def __contains__(self, key: str) -> bool:
        """Check if key exists."""
        try:
            _ = self[key]
            return True
        except KeyError:
            return False


CFG: Dict[str, Any] = _CFGDict()


# Валідації
try:
    if not CFG.get("tg_token"):
        raise RuntimeError("Environment variable TELEGRAM_BOT_TOKEN is not set. Please check your .env file.")
except Exception:
    # If Pydantic validation already caught it, skip
    if not _USE_PYDANTIC:
        raise


# Для діагностики у логах (за бажання)
def debug_print_cfg() -> None:
    try:
        slots = [
            {"model": s["model"], "base": s.get("base")}
            for s in CFG.get("or_slots", [])
        ]
        print(
            "[CFG] symbols="
            f"{CFG['symbols']} | or_slots={slots} | tz={CFG['tz']} | "
            f"analyze_limit={CFG['analyze_limit']}"
        )
    except Exception:
        # Логи діагностики не мають ламати основний запуск
        pass


__all__ = ["CFG", "debug_print_cfg"]
