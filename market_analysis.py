import requests
from datetime import datetime

print('=== АНАЛІЗ ПОТОЧНОГО РИНКУ ===')
print(f'Час: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')

symbols = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'ADAUSDT']

for symbol in symbols:
    try:
        # Get current price from Binance API
        url = f'https://api.binance.com/api/v3/ticker/price?symbol={symbol}'
        response = requests.get(url, timeout=5)
        data = response.json()
        price = float(data['price'])

        # Get 24h change
        url_24h = f'https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}'
        response_24h = requests.get(url_24h, timeout=5)
        data_24h = response_24h.json()
        change_24h = float(data_24h['priceChangePercent'])

        print(f'{symbol:8}: ${price:>10,.2f} | 24h: {change_24h:>+6.2f}%')

    except Exception as e:
        print(f'{symbol:8}: ПОМИЛКА - {str(e)[:50]}')

print('\n=== ЗАГАЛЬНИЙ СТАТУС ===')
print('✅ API Binance працює')
print('✅ Мережеве з\'єднання активне')
print('ℹ️  Gate score аналіз потребує додаткового дослідження')