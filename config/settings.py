"""Pydantic Settings configuration for CCBV3.8."""
from __future__ import annotations

import json
import os
from typing import List, Optional

from dotenv import load_dotenv
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv(".env", override=True)


def _parse_or_slots_from_env() -> List[dict]:
    """
    Підтримує три формати:
    1) OR_SLOTS як JSON
    2) OR_SLOTS як простий список
    3) Пара змінних через кому
    """
    slots: List[dict] = []
    raw = (os.getenv("OR_SLOTS", "") or "").strip()
    
    if raw:
        if raw.startswith("["):
            try:
                data = json.loads(raw)
                for s in data:
                    key = s.get("key") or s.get("api_key")
                    model = s.get("model")
                    base = s.get("base")
                    timeout = s.get("timeout")
                    if key and model:
                        slots.append({
                            "key": key,
                            "model": model,
                            "base": base,
                            "timeout": timeout,
                        })
            except Exception:
                pass
        else:
            parts = [p.strip() for p in raw.split(",") if p.strip()]
            for p in parts:
                if ":" in p:
                    k, m = [x.strip() for x in p.split(":", 1)]
                    if k and m:
                        slots.append({"key": k, "model": m})
    
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
    
    base_default = os.getenv("OPENROUTER_BASE", "https://openrouter.ai/api/v1")
    timeout_default = int(os.getenv("OPENROUTER_TIMEOUT", "30") or 30)
    normalized: List[dict] = []
    for s in slots:
        if not (s.get("key") and s.get("model")):
            continue
        normalized.append({
            "key": s["key"],
            "model": s["model"],
            "base": s.get("base") or base_default,
            "timeout": int(s.get("timeout") or timeout_default),
        })
    return normalized


class TelegramSettings(BaseSettings):
    """Telegram bot settings."""
    model_config = SettingsConfigDict(env_prefix="TELEGRAM_", case_sensitive=False)
    
    bot_token: str = Field(
        ...,
        alias="BOT_TOKEN",
        validation_alias=AliasChoices("BOT_TOKEN", "TELEGRAM_BOT_TOKEN"),
    )
    chat_id: Optional[str] = Field(
        None,
        alias="CHAT_ID",
        validation_alias=AliasChoices("CHAT_ID", "TELEGRAM_CHAT_ID"),
    )
    mode: str = Field(
        "polling",
        alias="BOT_MODE",
        validation_alias=AliasChoices("BOT_MODE", "TELEGRAM_BOT_MODE"),
    )
    webhook_url: Optional[str] = Field(
        None,
        alias="WEBHOOK_URL",
        validation_alias=AliasChoices("WEBHOOK_URL", "TELEGRAM_WEBHOOK_URL"),
    )
    port: int = Field(
        8080,
        alias="PORT",
        validation_alias=AliasChoices("PORT", "TELEGRAM_PORT"),
    )


class OpenRouterSettings(BaseSettings):
    """OpenRouter LLM settings."""
    model_config = SettingsConfigDict(env_prefix="OPENROUTER_", case_sensitive=False)
    
    keys: List[str] = Field(default_factory=list, alias="KEYS")
    model: str = Field("deepseek/deepseek-chat", alias="MODEL")
    base_url: str = Field("https://openrouter.ai/api/v1", alias="BASE")
    timeout: int = Field(30, alias="TIMEOUT")
    max_tokens: int = Field(1024, alias="MAX_TOKENS")
    
    @field_validator("keys", mode="before")
    @classmethod
    def parse_keys(cls, v):
        if isinstance(v, str):
            return [k.strip() for k in v.split(",") if k.strip()]
        return v or []


class TradingSettings(BaseSettings):
    """Trading strategy settings."""
    model_config = SettingsConfigDict(env_prefix="TRADING_", case_sensitive=False)
    
    min_rr: float = Field(1.5, alias="MIN_RR")
    risk_per_trade: float = Field(0.0075, alias="RISK_PER_TRADE")
    partial_tp_pct: float = Field(0.5, alias="PARTIAL_TP_PCT")
    move_be_at_rr: float = Field(1.0, alias="MOVE_BE_AT_RR")
    atr_sl_mult: float = Field(2.0, alias="ATR_SL_MULT")


class IndicatorSettings(BaseSettings):
    """Technical indicator thresholds."""
    model_config = SettingsConfigDict(env_prefix="INDICATOR_", case_sensitive=False)
    
    atr_min: float = Field(0.004, alias="ATR_MIN")
    rsi_long_min: float = Field(50.0, alias="RSI_LONG_MIN")
    rsi_short_max: float = Field(50.0, alias="RSI_SHORT_MAX")
    adx_min: float = Field(18.0, alias="ADX_MIN")
    bbw_min: float = Field(0.015, alias="BBW_MIN")
    vol_rel_min: float = Field(1.2, alias="VOL_REL_MIN")
    vwap_dist_min: float = Field(0.0015, alias="VWAP_DIST_MIN")


class DatabaseSettings(BaseSettings):
    """Database settings."""
    model_config = SettingsConfigDict(env_prefix="DB_", case_sensitive=False)
    
    path: str = Field("/data/bot.db", alias="PATH")
    pool_size: int = Field(5, alias="POOL_SIZE")


class Settings(BaseSettings):
    """Main settings class aggregating all settings."""
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore")
    
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    openrouter: OpenRouterSettings = Field(default_factory=OpenRouterSettings)
    trading: TradingSettings = Field(default_factory=TradingSettings)
    indicator: IndicatorSettings = Field(default_factory=IndicatorSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    
    # Direct env vars
    tz_name: str = Field("Europe/Kyiv", alias="TZ_NAME")
    default_locale: str = Field("uk", alias="DEFAULT_LOCALE")
    symbols: str = Field(default="BTCUSDT", alias="MONITORED_SYMBOLS")
    metals_symbols: str = Field(default="XAUUSD,XAGUSD", alias="METALS_SYMBOLS")
    analyze_timeframe: str = Field("15m", alias="ANALYZE_TIMEFRAME")
    analyze_limit: int = Field(150, alias="ANALYZE_LIMIT")
    autopost_interval_sec: int = Field(300, alias="AUTOPOST_INTERVAL_SEC")
    autopost_cooldown_min: int = Field(30, alias="AUTOPOST_COOLDOWN_MIN")
    orderbook_enabled: bool = Field(True, alias="ORDERBOOK_ENABLED")
    orderbook_levels: int = Field(50, alias="ORDERBOOK_LEVELS")
    orderbook_ttl_sec: int = Field(20, alias="ORDERBOOK_TTL_SEC")
    orderbook_bucket_pct: float = Field(0.10, alias="ORDERBOOK_BUCKET_PCT")
    wall_usdt_threshold: float = Field(2000000.0, alias="WALL_USDT_THRESHOLD")
    wall_near_pct: float = Field(1.0, alias="WALL_NEAR_PCT")
    
    @field_validator("symbols", "metals_symbols", mode="after")
    @classmethod
    def parse_symbols(cls, v):
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v
    
    @field_validator("orderbook_enabled", mode="before")
    @classmethod
    def parse_bool(cls, v):
        if isinstance(v, str):
            return v.lower() in ("1", "true", "yes", "on")
        return bool(v)
    
    @property
    def or_slots(self) -> List[dict]:
        """Get OpenRouter slots from environment."""
        return _parse_or_slots_from_env()
    
    @property
    def tg_token(self) -> str:
        """Backward compatibility alias."""
        return self.telegram.bot_token
    
    @property
    def tg_chat_id(self) -> Optional[str]:
        """Backward compatibility alias."""
        return self.telegram.chat_id
    
    @property
    def bot_mode(self) -> str:
        """Backward compatibility alias."""
        return self.telegram.mode
    
    @property
    def webhook_url(self) -> Optional[str]:
        """Backward compatibility alias."""
        return self.telegram.webhook_url
    
    @property
    def port(self) -> int:
        """Backward compatibility alias."""
        return self.telegram.port
    
    @property
    def tz(self) -> str:
        """Backward compatibility alias."""
        return self.tz_name
    
    @property
    def or_base(self) -> str:
        """Backward compatibility alias."""
        return self.openrouter.base_url
    
    @property
    def or_timeout(self) -> int:
        """Backward compatibility alias."""
        return self.openrouter.timeout

    def __init__(self, **values):
        super().__init__(**values)
        # Removed logging of TELEGRAM_BOT_TOKEN to prevent exposure
        # print("Loaded TELEGRAM_BOT_TOKEN:", self.telegram.bot_token)


# Global settings instance
settings = Settings()
