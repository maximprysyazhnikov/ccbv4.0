import sqlite3
import os
from datetime import datetime

print('=== СИСТЕМНА ДІАГНОСТИКА CCBV3.8 ===')

# Database health check
conn = sqlite3.connect('storage/bot.db')
cursor = conn.cursor()

# Check table structure
tables = ['trades', 'user_settings', 'settings', 'user_verified', 'signals']
for table in tables:
    try:
        cursor.execute(f'PRAGMA table_info({table})')
        cols = cursor.fetchall()
        print(f'✓ Таблиця {table}: {len(cols)} колонок')
    except Exception as e:
        print(f'✗ Таблиця {table}: ПОМИЛКА - {e}')

# Check data integrity
cursor.execute('SELECT COUNT(*) FROM trades')
total_trades = cursor.fetchone()[0]
print(f'Загальна кількість трейдів в БД: {total_trades}')

cursor.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN'")
open_trades = cursor.fetchone()[0]
print(f'Відкритих трейдів: {open_trades}')

cursor.execute('SELECT COUNT(*) FROM user_settings')
user_count = cursor.fetchone()[0]
print(f'Кількість користувачів: {user_count}')

# Check recent activity
cursor.execute("SELECT COUNT(*) FROM trades WHERE opened_at >= datetime('now', '-24 hours')")
daily_trades = cursor.fetchone()[0]
print(f'Трейдів за останні 24 години: {daily_trades}')

# Check database size
db_size = os.path.getsize('storage/bot.db') / (1024 * 1024)  # MB
print(f'Розмір БД: {db_size:.2f} MB')

conn.close()

# Check log files
print('\n=== ПЕРЕВІРКА ЛОГІВ ===')
if os.path.exists('logs'):
    log_files = os.listdir('logs')
    print(f'Знайдено {len(log_files)} лог-файлів')
    for log in sorted(log_files)[:5]:  # Show first 5
        size = os.path.getsize(f'logs/{log}') / 1024  # KB
        print(f'  {log}: {size:.1f} KB')
else:
    print('✗ Директорія logs не знайдена')

# Check reports
print('\n=== ПЕРЕВІРКА ЗВІТІВ ===')
if os.path.exists('reports'):
    report_files = os.listdir('reports')
    print(f'Знайдено {len(report_files)} звітів')
    recent_reports = sorted([f for f in report_files if f.endswith('.md')],
                           key=lambda x: os.path.getmtime(f'reports/{x}'),
                           reverse=True)[:3]
    for report in recent_reports:
        mtime = datetime.fromtimestamp(os.path.getmtime(f'reports/{report}')).strftime('%Y-%m-%d %H:%M')
        print(f'  {report} (змінено: {mtime})')
else:
    print('✗ Директорія reports не знайдена')

print('\n=== СИСТЕМНІ МЕТРИКИ ===')
print(f'Поточна дата: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
print('Часовий пояс: Europe/Kyiv')