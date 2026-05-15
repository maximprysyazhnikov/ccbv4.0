# services/signal_filter.py

def should_send_signal(signal: dict, user_settings: dict, price_api) -> (bool, str):
    """
    Фільтрує сигнал перед автопостом.
    :param signal: dict з entry, sl, tp, rr, direction, tf, symbol
    :param user_settings: dict з autopost_rr, autopost_tf тощо
    :param price_api: об'єкт для отримання поточної ціни/ATR/тренду
    :return: (True/False, причина)
    """
    # 1. RR-фільтр
    rr_min = float(user_settings.get('autopost_rr', 1.5))
    if signal.get('rr', 0) < rr_min:
        return False, f"RR {signal.get('rr')} < мінімального {rr_min}"

    # 2. Відстань до Entry
    current_price = price_api.get_price(signal['symbol'])
    entry = float(signal['entry'])
    if abs(current_price - entry) / entry > 0.02:
        return False, "Entry далеко від ринку (>2%)"

    # 3. Волатильність (ATR)
    atr = price_api.get_atr(signal['symbol'], signal['tf'])
    if atr > 0.01 * entry:
        return False, "Висока волатильність (ATR)"

    # 4. Тренд-фільтр
    if not price_api.trend_aligned(signal['symbol'], signal['tf'], signal['direction']):
        return False, "Сигнал проти тренду"

    # 5. (Опціонально) Часовий фільтр, новини, orderbook тощо

    return True, "OK"

# Можна додати mock-клас для тесту:
class MockPriceAPI:
    def get_price(self, symbol):
        return 100.0
    def get_atr(self, symbol, tf):
        return 0.5
    def trend_aligned(self, symbol, tf, direction):
        return True
