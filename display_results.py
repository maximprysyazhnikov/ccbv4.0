import json

with open('analysis_results.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# Print key trading metrics
print('📊 PHASE 2 DATA ANALYSIS RESULTS')
print('=' * 40)

if 'key_metrics' in data and 'overall_performance' in data['key_metrics']:
    perf = data['key_metrics']['overall_performance']
    print(f'Total Trades: {perf["total_trades"]}')
    print(f'Win Rate: {perf["win_rate_pct"]:.2f}%')
    print(f'Total P&L: ${perf["total_pnl"]:.4f}')
    print(f'Avg P&L per Trade: ${perf["avg_pnl_per_trade"]:.4f}')
    print(f'Best Trade: ${perf["best_trade"]:.4f}')
    print(f'Worst Trade: ${perf["worst_trade"]:.4f}')

if 'key_metrics' in data and 'risk_metrics' in data['key_metrics']:
    risk = data['key_metrics']['risk_metrics']
    print(f'\nRisk Metrics:')
    print(f'Sharpe Ratio: {risk["sharpe_ratio"]:.4f}')
    print(f'Return Std Dev: ${risk["std_dev"]:.6f}')

if 'key_metrics' in data and 'streaks' in data['key_metrics']:
    streaks = data['key_metrics']['streaks']
    print(f'\nStreaks:')
    print(f'Current Streak: {streaks["current_streak"]}')
    print(f'Max Win Streak: {streaks["max_win_streak"]}')
    print(f'Max Loss Streak: {streaks["max_loss_streak"]}')

print(f'\nRecommendations: {len(data.get("recommendations", []))}')
for rec in data.get('recommendations', []):
    print(f'  {rec["priority"]}: {rec["issue"]}')