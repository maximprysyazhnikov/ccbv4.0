def calculate_weighted_gate_score(indicators: dict, market_regime: str = 'moderate') -> float:
    """
    Розрахунок зваженого gate score з урахуванням market regime

    Args:
        indicators: словник з усіма індикаторами
        market_regime: 'conservative', 'moderate', 'aggressive'

    Returns:
        gate_score: 0.0 to 1.0 (чим вище, тим краще сигнал)
    """
    # Dynamic thresholds based on market regime
    thresholds = {
        'conservative': 0.75,
        'moderate': 0.70,
        'aggressive': 0.65
    }

    threshold = thresholds.get(market_regime, 0.70)

    # Weights for different categories
    weights = {
        'trend': 0.25,
        'momentum': 0.25,
        'volatility': 0.20,
        'volume': 0.30
    }

    scores = {
        'trend_score': calculate_trend_score(indicators),
        'momentum_score': calculate_momentum_score(indicators),
        'volatility_score': calculate_volatility_score(indicators),
        'volume_score': calculate_volume_score(indicators)
    }

    # Weighted average
    total_score = sum(scores[cat] * weights[cat.split('_')[0]] for cat in scores.keys())

    return total_score

def calculate_trend_score(indicators: dict) -> float:
    """Розрахунок trend score (EMA, SMA, ADX)"""
    score = 0.0
    max_score = 3.0

    # EMA trend alignment (1 point)
    if indicators.get('ema_trend_aligned', False):
        score += 1.0

    # ADX strength (1 point)
    if indicators.get('adx', 0) > 20:
        score += 1.0

    # SMA confirmation (1 point)
    if indicators.get('sma_trend_aligned', False):
        score += 1.0

    return score / max_score

def calculate_momentum_score(indicators: dict) -> float:
    """Розрахунок momentum score (RSI, StochRSI, MACD, CCI)"""
    score = 0.0
    max_score = 4.0

    # RSI in good range (1 point)
    rsi = indicators.get('rsi', 50)
    if (rsi > 30 and rsi < 70) or (rsi < 30 or rsi > 70):  # oversold/overbought preferred
        score += 1.0

    # MACD alignment (1 point)
    if indicators.get('macd_aligned', False):
        score += 1.0

    # StochRSI confirmation (1 point)
    if indicators.get('stoch_rsi_aligned', False):
        score += 1.0

    # CCI extreme values (1 point)
    cci = indicators.get('cci', 0)
    if abs(cci) > 100:  # extreme readings
        score += 1.0

    return score / max_score

def calculate_volatility_score(indicators: dict) -> float:
    """Розрахунок volatility score (Bollinger, VWAP, Pivots)"""
    score = 0.0
    max_score = 3.0

    # Bollinger position (1 point)
    bb_pos = indicators.get('bollinger_position', 0.5)
    if bb_pos < 0.2 or bb_pos > 0.8:  # near bands
        score += 1.0

    # VWAP alignment (1 point)
    if indicators.get('vwap_aligned', False):
        score += 1.0

    # Pivot levels (1 point)
    if indicators.get('pivot_aligned', False):
        score += 1.0

    return score / max_score

def calculate_volume_score(indicators: dict) -> float:
    """Розрахунок volume score (Volume, MFI, L/S Ratio)"""
    score = 0.0
    max_score = 3.0

    # Volume confirmation (1 point)
    if indicators.get('volume_confirmed', False):
        score += 1.0

    # MFI in good range (1 point)
    mfi = indicators.get('mfi', 50)
    if mfi < 30 or mfi > 70:  # oversold/overbought
        score += 1.0

    # L/S Ratio sentiment (1 point)
    ls_ratio = indicators.get('ls_ratio', 1.0)
    if ls_ratio > 1.05 or ls_ratio < 0.95:  # significant imbalance
        score += 1.0

    return score / max_score

def detect_market_regime(indicators: dict) -> str:
    """Визначення market regime для dynamic thresholds"""
    adx = indicators.get('adx', 20)
    ls_ratio = indicators.get('ls_ratio', 1.0)

    if adx > 25:
        if ls_ratio > 1.1:
            return 'bullish'
        elif ls_ratio < 0.9:
            return 'bearish'

    if adx < 20:
        return 'sideways'

    return 'moderate'