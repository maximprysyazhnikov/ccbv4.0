# ccbv4.0

AI Crypto CAT Bot

Telegram-бот для аналізу крипторинку:
- Графіки, обʼєми, стакан, теханаліз, новини
- GPT-аналітик для створення звітів
- Push-алерти при зміні ключових метрик
- Експорт у Markdown/HTML/PDF

## 🚀 Запуск локально

```bash
pip install -r requirements.txt
python main.py
```

## Railway

Railway стартує бота командою `python main.py`.

Required variables:

```text
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_chat_id
OPENROUTER_KEYS=your_openrouter_key
DB_PATH=/data/bot_live_v2.db
TZ_NAME=Europe/Kyiv
```

Recommended: attach a Railway volume mounted at `/data` so SQLite state survives redeploys.

Trading/runtime defaults live in `config/trading_defaults.py`. Railway variables still
override those defaults, so keep only secrets and environment-specific values in Railway
unless you need a quick runtime override.

