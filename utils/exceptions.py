"""Custom exception hierarchy for CCBV3.8."""
from __future__ import annotations


class CCBVError(Exception):
    """Base exception for CCBV application."""
    pass


class ConfigurationError(CCBVError):
    """Invalid configuration."""
    pass


class APIError(CCBVError):
    """External API errors."""
    pass


class OpenRouterError(APIError):
    """OpenRouter-specific errors."""
    pass


class BinanceError(APIError):
    """Binance-specific errors."""
    pass


class TradingError(CCBVError):
    """Trading logic errors."""
    pass


class DatabaseError(CCBVError):
    """Database operation errors."""
    pass


class ValidationError(CCBVError):
    """Data validation errors."""
    pass
