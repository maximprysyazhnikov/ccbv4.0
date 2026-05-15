import asyncio
from services.scalping_sources import _collect_symbol_indicators, _evaluate_scalping_gate, _get_scalping_thresholds

async def main():
    symbol = 'BNBUSDT'
    timeframe = '5m'
    thresholds = _get_scalping_thresholds()
    df, ind, last = await _collect_symbol_indicators(symbol, timeframe)
    direction = 'LONG' if ind['ema50'] >= ind['ema200'] else 'SHORT'
    gate_score, gate_total, gate_pct, reasons, gate_details = _evaluate_scalping_gate(ind, direction, thresholds, last)
    print('symbol:', symbol)
    print('direction:', direction)
    print('gate_score:', gate_score, 'gate_total:', gate_total, f'gate_pct:{gate_pct:.1f}%')
    print('\nreasons:')
    for r in reasons:
        print('-', r)
    print('\n gate_details:')
    for k,v in gate_details.items():
        ok = v.get('ok')
        print(f"{k}: ok={ok}, details={v}")

if __name__ == '__main__':
    asyncio.run(main())
