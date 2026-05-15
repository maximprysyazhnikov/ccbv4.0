import sqlite3
import pandas as pd

conn = sqlite3.connect('storage/bot.db')
df = pd.read_sql_query('SELECT symbol, direction, status, trade_mode, pnl_usd, rr, opened_at, closed_at, gate_score, gate_total FROM trades WHERE opened_at >= date("now", "-30 days") ORDER BY opened_at DESC', conn)

print('=== ТОРГОВІ СТАТИСТИКИ ЗА ОСТАННІ 30 ДНІВ ===')
print(f'Загальна кількість трейдів: {len(df)}')

if len(df) > 0:
    closed_trades = df[df['status'] == 'CLOSED']
    print(f'Закритих трейдів: {len(closed_trades)}')

    if len(closed_trades) > 0:
        win_rate = (closed_trades['pnl_usd'] > 0).mean() * 100
        avg_rr = closed_trades['rr'].mean()
        total_pnl = closed_trades['pnl_usd'].sum()

        print(f'Win Rate: {win_rate:.1f}%')
        print(f'Середній RR: {avg_rr:.2f}')
        print(f'Загальний P&L: ${total_pnl:.2f}')

        print('\n=== ПО СИМВОЛАМ ===')
        symbol_stats = closed_trades.groupby('symbol').agg({
            'pnl_usd': ['count', 'sum', lambda x: (x > 0).mean() * 100]
        }).round(2)
        symbol_stats.columns = ['Трейди', 'P&L', 'Win Rate %']
        print(symbol_stats.sort_values('Трейди', ascending=False).head(10))

print('\n=== АНАЛІЗ GATE SCORES ===')
gate_analysis = df.groupby('gate_score').size()
print('Розподіл gate scores:')
for score, count in gate_analysis.items():
    pct = count / len(df) * 100
    print(f'  {score}/13: {count} трейдів ({pct:.1f}%)')

conn.close()