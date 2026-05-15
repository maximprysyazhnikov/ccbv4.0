# MAXPILOT — Повний аналіз проекту CCBV3.8

> **Дата**: січень 2026  
> **Автор аналізу**: GitHub Copilot  
> **Версія проекту**: 3.8

---

## 📌 ЗАГАЛЬНИЙ ОПИС

**CCBV3.8** — це повнофункціональний Telegram-бот для автоматизованого криптотрейдингу з AI-аналізом, що включає:

### 🚀 Основні можливості:
- **Ринкові дані**: Binance Spot API (OHLCV, стакан ордерів, L/S Ratio, новини)
- **Технічний аналіз**: 15+ індикаторів (EMA, RSI, MACD, ADX, Bollinger, OBV, MFI, ATR, CCI, StochRSI, Pivots, VWAP, Volume Ratio)
- **AI-аналіз**: LLM інтеграція через OpenRouter (DeepSeek, GPT-4, Claude) для генерації торгових планів
- **Автоматизований трейдинг**: Paper trading з реалістичним P&L, risk management
- **Сигнальна система**: Gate logic (12 критеріїв), автопостінг, нейтральний режим
- **Скальпінг**: Фіксовані SL/TP %, slippage adjustment, quality gate
- **Статистика**: KPI, winrate tracking, model ranking, outcome analysis
- **Telegram інтерфейс**: 15+ команд, inline клавіатури, callback handlers
- **База даних**: SQLite з WAL mode, міграції, аудит, інструменти
- **Моніторинг**: Push-алерти, логи, smoke тестування, debugging tools

### 🎯 Ключові особливості:
- **Гібридна архітектура**: Сумісність v2/v3 з плавною міграцією
- **Модульна структура**: 15+ директорій, 100+ файлів, чітке розділення відповідальностей


- **Продакшн-ready**: Конфігурація через Pydantic, error handling, logging
- **Розширюваність**: Plugin-система для нових індикаторів, моделей, брокерів
- **Тестування**: Pytest suite, smoke tests, database tools

### 📊 Метрики проекту:
- **Строки коду**: ~15,000+ (Python)
- **Файлів**: 120+
- **Команд бота**: 15+
- **Кнопок UI**: 25+
- **Таблиць БД**: 5+
- **Індикаторів**: 15+
- **LLM моделей**: Підтримка 10+ через OpenRouter

---

## 🏗 АРХІТЕКТУРА

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              TELEGRAM BOT                                       │
│  (main.py → telegram_bot/handlers/ → panel.py + panel_neutral.py)               │
└─────────────────────────────┬───────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│  MARKET DATA  │   │   SERVICES    │   │    GPT/AI     │
│  (Binance API)│   │  (autopost,   │   │ (analyzer,    │
│               │   │   scalping,   │   │  decider)     │
│               │   │   trade_engine│   │               │
└───────┬───────┘   └───────┬───────┘   └───────┬───────┘
        │                   │                   │
        └───────────────────┼───────────────────┘
                            ▼
               ┌─────────────────────┐
               │      STORAGE        │
               │   (SQLite DB)       │
               │ storage/bot.db      │
               │ data/users.db       │
               └─────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│    TRADER     │   │     STATS     │   │   SIGNAL     │
│ (paper_trade, │   │ (model_ranker,│   │   TOOLS      │
│  risk_manager)│   │ outcome_resolver)│   │(backfill,   │
└───────────────┘   └───────────────┘   └───────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│   SCHEDULER   │   │    ALERTS     │   │    UTILS     │
│ (periodic jobs│   │ (push_alerts) │   │ (db, logging,│
│  autopost)    │   │               │   │  settings)   │
└───────────────┘   └───────────────┘   └───────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│   SCRIPTS     │   │   MIGRATIONS  │   │    CONFIG    │
│ (db tools,    │   │ (SQL scripts) │   │ (Pydantic    │
│  smoke tests) │   │               │   │  settings)   │
└───────────────┘   └───────────────┘   └───────────────┘
```

---

## 📁 СТРУКТУРА ПРОЕКТУ

### 🔷 КОРІНЬ

| Файл | Призначення |
|------|-------------|
| `main.py` | Точка входу. Ініціалізація бота, JobQueue, обробники |
| `core_config.py` | Централізована конфігурація CFG (dict) |
| `requirements.txt` | Залежності production |
| `requirements-dev.txt` | Залежності dev (pytest, etc.) |
| `pytest.ini` | Конфіг тестів |

### 🔷 /config

| Файл | Призначення |
|------|-------------|
| `settings.py` | Pydantic Settings з env-валідацією |

**Класи Settings**:
- `TelegramSettings` — BOT_TOKEN, CHAT_ID, MODE
- `OpenRouterSettings` — KEYS, MODEL, BASE, TIMEOUT
- `TradingSettings` — MIN_RR, RISK_PER_TRADE, ATR_SL_MULT
- `IndicatorSettings` — ATR_MIN, RSI_LONG_MIN, ADX_MIN, etc.
- `DatabaseSettings` — DB_PATH

### 🔷 /telegram_bot

| Файл | Призначення |
|------|-------------|
| `panel.py` | Inline-клавіатура налаштувань |
| `bot.py` | Допоміжні функції бота |

**Налаштування панелі**:
- Autopost ON/OFF
- Scalping mode ON/OFF + SL%/TP%/Slippage%
- Monitored Symbols (custom list)
- Timeframe (5m, 15m, 1h, 4h, 1d)
- Autopost RR threshold
- Model selection (auto / specific)
- Locale (uk/en)
- Daily/Winrate trackers

### 🔷 /telegram_bot/handlers

| Файл | Призначення |
|------|-------------|
| `ai_commands.py` | `/ai`, `/req`, `/analyze` — AI-аналіз |
| `commands.py` | `/start`, `/help`, `/ping`, `/news`, `/guide` |
| `panel_handlers.py` | Callback обробники панелі |
| `kpi_handlers.py` | `/kpi` — статистика трейдів |
| `top_handlers.py` | `/top` — топ-20 пар + меню символу |
| `callbacks.py` | Загальні callback handlers |
| `helpers.py` | Допоміжні функції (_send, _llm_allowed, etc.) |
| `register.py` | Реєстрація всіх handlers |
| `handlers_addons.py` | **🆕** `/ls`, `/sentiment`, callback `on_cb_ls` |

### 🔷 /services

| Файл | Призначення |
|------|-------------|
| `scalping_sources.py` | **🔑 КЛЮЧОВИЙ** — Збір індикаторів + gate logic |
| `trade_engine.py` | Відкриття/закриття угод у БД |
| `analyzer_core.py` | Розрахунок індикаторів (EMA, RSI, ADX, etc.) |
| `signal_closer.py` | Перевірка TP/SL хітів |
| `signal_sync.py` | Синхронізація сигналів |
| `kpi.py` | KPI розрахунки |
| `pnl.py` | P&L калькуляції |

### 🔷 /services/autopost

| Файл | Призначення |
|------|-------------|
| `core.py` | **🔑 КЛЮЧОВИЙ** — `run_autopost_once()` |
| `indicators.py` | `ind_summary()`, `build_panel_lite()` |
| `formatting.py` | Форматування чисел, відсотків |
| `sources.py` | Джерела сигналів |

### 🔷 /market_data

| Файл | Призначення |
|------|-------------|
| `binance_data.py` | `get_ohlcv()`, `get_24h_ticker()`, `get_latest_price()` |
| `binance.py` | Допоміжні функції Binance |
| `binance_rank.py` | Ранжування пар |
| `candles.py` | Обгортка для свічок |
| `orderbook.py` | Стакан ордерів (повний) |
| `orderbook_light.py` | Стакан ордерів (лайт) |
| `news.py` | Парсинг новин |
| `long_short_ratio.py` | **🆕** Long/Short Ratio з Binance Futures API |

### 🔷 /gpt_analyst

| Файл | Призначення |
|------|-------------|
| `full_analyzer.py` | `run_full_analysis()` — LLM аналіз |
| `llm_client.py` | Клієнт OpenRouter |
| `symbol_screener.py` | Скринінг символів |

### 🔷 /gpt_decider

| Файл | Призначення |
|------|-------------|
| `decider.py` | `decide_from_markdown()` — парсинг LLM відповіді |

**Extracted fields**:
- RR (risk/reward)
- Confidence %
- Direction (LONG/SHORT/NEUTRAL)
- Entry, SL, TP levels

### 🔷 /router

| Файл | Призначення |
|------|-------------|
| `analyzer_router.py` | Round-robin маршрутизація API keys |

**Route dataclass**:
```python
@dataclass
class Route:
    api_key: str
    model: str
    base: str
    timeout: int
```

### 🔷 /scheduler

| Файл | Призначення |
|------|-------------|
| `runner.py` | `start_autopost()` — реєстрація JobQueue tasks |
| `periodic_runner.py` | Періодичні задачі |
| `local_top5_job.py` | Топ-5 локальний джоб |
| `screener_job.py` | Скринер джоб |

**Scheduled jobs**:
- `autopost_scan` — кожні N сек (default 300)
- `signal_closer` — кожні 120 сек

### 🔷 /utils

| Файл | Призначення |
|------|-------------|
| `db.py` | **🔑 КЛЮЧОВИЙ** — Connection pool, `get_conn()` |
| `user_settings.py` | `get_user_settings()`, `set_user_settings()` |
| `ta_formatter.py` | `format_ta_report()` — Markdown індикаторів |
| `symbol_validator.py` | Валідація символів через Binance API |
| `openrouter.py` | OpenRouter API клієнт |
| `settings.py` | `get_setting()`, `set_setting()` |
| `news_fetcher.py` | Парсер новин |

### 🔷 /alerts

| Файл | Призначення |
|------|-------------|
| `push_alerts.py` | `run_alerts_once()` — алерти про drawdown, winrate |
| `signal_registry.py` | Реєстр сигналів |

### 🔷 /trader

| Файл | Призначення |
|------|-------------|
| `broker.py` | Paper trade заглушка |
| `paper_trade.py` | Симуляція трейдів |
| `risk_manager.py` | Ризик-менеджмент |

### 🔷 /storage

| Файл | Призначення |
|------|-------------|
| `bot.db` | SQLite база даних |

---

## 📊 ІНДИКАТОРИ (повний список)

### services/scalping_sources.py + utils/ta_formatter.py + services/analyzer_core.py

| # | Індикатор | Формула | Використання |
|---|-----------|---------|--------------|
| 1 | **EMA** | $EMA_t = \alpha \cdot Price_t + (1-\alpha) \cdot EMA_{t-1}$ | Тренд |
| 2 | **SMA** | $SMA = \frac{\sum_{i=1}^{n} Price_i}{n}$ | Тренд |
| 3 | **RSI** | $RSI = 100 - \frac{100}{1 + RS}$, $RS = \frac{AvgGain}{AvgLoss}$ | Моментум |
| 4 | **StochRSI** | $StochRSI = \frac{RSI - RSI_{min}}{RSI_{max} - RSI_{min}}$ | Моментум |
| 5 | **MACD** | $MACD = EMA_{12} - EMA_{26}$, $Signal = EMA_9(MACD)$ | Моментум |
| 6 | **ATR** | $ATR = EMA_{14}(TrueRange)$ | Волатильність |
| 7 | **ADX** | $ADX = EMA_{14}(DX)$, $DX = \frac{|+DI - -DI|}{+DI + -DI}$ | Сила тренду |
| 8 | **CCI** | $CCI = \frac{TP - SMA(TP)}{0.015 \cdot MAD(TP)}$ | Моментум |
| 9 | **Bollinger** | $Upper = MA + 2\sigma$, $Lower = MA - 2\sigma$, $\%B = \frac{Price-Lower}{Upper-Lower}$ | Волатильність |
| 10 | **OBV** | $OBV_t = OBV_{t-1} + sign(\Delta Price) \cdot Volume$ | Обʼєм |
| 11 | **MFI** | $MFI = 100 - \frac{100}{1 + MFR}$, $MFR = \frac{PosMF}{NegMF}$ | Обʼєм |
| 12 | **Pivots** | $P = \frac{H+L+C}{3}$, $R1 = 2P-L$, $S1 = 2P-H$ | Рівні |
| 13 | **VWAP** | $VWAP = \frac{\sum (TP \cdot Vol)}{\sum Vol}$ | Ціновий рівень |
| 14 | **Volume Ratio** | $VolumeRatio = \frac{CurrentVol}{SMA_{20}(Vol)}$ | Обʼєм |
| 15 | **🆕 Long/Short Ratio** | $LSR = \frac{LongAccounts}{ShortAccounts}$ | Сентимент ринку |

---

## 🚀 GATE LOGIC (scalping_sources.py)

### Параметри за замовчуванням:
```python
SCALP_SL_PCT = 0.3%    # Stop Loss
SCALP_TP_PCT = 1.2%    # Take Profit  
SLIPPAGE_PCT = 0.08%   # Slippage
GATE_THRESHOLD = 0.4   # 40% індикаторів повинні пройти
```

### Gate checks (13 критеріїв):
| # | Критерій | LONG умова | SHORT умова |
|---|----------|------------|-------------|
| 1 | EMA trend | EMA50 > EMA200 | EMA50 < EMA200 |
| 2 | SMA trend | SMA7 > SMA25 | SMA7 < SMA25 |
| 3 | RSI | ∈ [50, 70) | ∈ (30, 50] |
| 4 | StochRSI | K > D, K < 80 | K < D, K > 20 |
| 5 | MACD | hist > 0, line > signal | hist < 0, line < signal |
| 6 | ADX | ≥ 18 | ≥ 18 |
| 7 | CCI | ∈ [-100, 200) | ∈ (-200, 100] |
| 8 | ATR% | ∈ [0.4%, 3.0%] | ∈ [0.4%, 3.0%] |
| 9 | Bollinger %B | < 0.7 | > 0.3 |
| 10 | VWAP | Price ~ VWAP (±1%) | Price ~ VWAP (±1%) |
| 11 | Volume | Ratio ≥ 1.2x | Ratio ≥ 1.2x |
| 12 | MFI | ∈ [20, 70) | ∈ (30, 80] |
| 13 | Pivots | Price ≥ S1 | Price ≤ R1 |

### RR калькуляція:
```python
# Базовий RR
RR_raw = TP_pct / SL_pct  # 1.2/0.3 = 4.0

# RR з урахуванням slippage
entry_adj = entry * (1 + slippage)  # LONG
tp_adj = tp * (1 - slippage)
RR_adj = (tp_adj - entry_adj) / (entry_adj - sl)

# RR для SHORT (з abs для коректного знаку)
RR = abs(tp - entry) / abs(entry - sl)
```

---

## 🗄 БАЗА ДАНИХ (SQLite)

### Таблиця `trades`:
```sql
CREATE TABLE trades (
    id INTEGER PRIMARY KEY,
    signal_id TEXT,
    symbol TEXT NOT NULL,
    timeframe TEXT,
    direction TEXT,           -- LONG/SHORT
    entry REAL,
    sl REAL,
    tp REAL,
    opened_at TEXT,
    closed_at TEXT,
    close_price REAL,
    close_reason TEXT,        -- TP/SL/MANUAL
    pnl_usd REAL,
    pnl_pct REAL,
    rr_planned REAL,
    rr_realized REAL,
    status TEXT,              -- OPEN/WIN/LOSS
    size_usd REAL,
    fees_bps INTEGER,
    trade_mode TEXT,          -- standard/scalping/ai
    indicators_json TEXT,     -- JSON з усіма індикаторами
    gate_score INTEGER,
    gate_total INTEGER,
    gate_pct REAL,
    slippage_pct REAL,
    rr_raw REAL,
    rr_adj REAL,
    ema50 REAL,
    ema200 REAL,
    atr_entry REAL,
    rr_target REAL
);
```

### Таблиця `user_settings`:
```sql
CREATE TABLE user_settings (
    id INTEGER PRIMARY KEY,
    user_id INTEGER UNIQUE,
    timeframe TEXT DEFAULT '15m',
    autopost INTEGER DEFAULT 0,
    autopost_tf TEXT,
    autopost_rr REAL DEFAULT 1.5,
    model_key TEXT DEFAULT 'auto',
    locale TEXT DEFAULT 'uk',
    daily_tracker INTEGER DEFAULT 0,
    winrate_tracker INTEGER DEFAULT 0,
    scalping_mode INTEGER DEFAULT 0,
    scalping_sl_pct REAL DEFAULT 0.3,
    scalping_tp_pct REAL DEFAULT 0.9,
    slippage_pct REAL DEFAULT 0.05,
    monitored_symbols TEXT
);
```

### Таблиця `settings`:
```sql
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
```

---

## 🔄 ПОТІК ДАНИХ

### 1. Autopost Flow:
```
JobQueue (кожні 300с)
    ↓
run_autopost_once()
    ↓
┌────────────────────────────────────────┐
│ Для кожного user з autopost=1:         │
│   1. Отримати user_settings            │
│   2. Визначити symbols + timeframe     │
│   3. Вибрати джерело:                  │
│      - scalping_mode → scalping_sources│
│      - standard → sources.py           │
│   4. Зібрати індикатори                │
│   5. Gate logic (12 checks)            │
│   6. Якщо gate_pct >= threshold:       │
│      - Відкрити trade в БД             │
│      - Відправити повідомлення         │
└────────────────────────────────────────┘
```

### 2. Signal Closer Flow:
```
JobQueue (кожні 120с)
    ↓
check_and_close_signals()
    ↓
┌────────────────────────────────────────┐
│ Для кожного OPEN trade:                │
│   1. Отримати поточну ціну             │
│   2. Перевірити TP/SL хіт:             │
│      LONG: price >= TP → WIN           │
│             price <= SL → LOSS         │
│      SHORT: price <= TP → WIN          │
│              price >= SL → LOSS        │
│   3. Оновити trade в БД                │
│   4. Відправити сповіщення             │
└────────────────────────────────────────┘
```

### 3. AI Analysis Flow (/ai command):
```
User: /ai BTCUSDT 1h
    ↓
ai_commands.py
    ↓
┌────────────────────────────────────────┐
│ 1. get_ohlcv(BTCUSDT, 1h, 150)         │
│ 2. format_ta_report() → 15 indicators  │
│ 3. pick_route() → LLM endpoint         │
│ 4. chat_completion() → AI plan JSON    │
│ 5. Parse: direction, entry, sl, tp,    │
│    tp2, key_levels, signals_bullish,   │
│    signals_bearish, confidence         │
│ 6. Calculate RR                        │
│ 7. Send formatted message              │
│ 8. 🆕 Save trade to DB (if LONG/SHORT) │
│    → trade_mode="ai"                   │
└────────────────────────────────────────┘

Auto-close (кожні 60с):
┌────────────────────────────────────────┐
│ auto_close_tp_sl_job():                │
│   - Перевіряє всі OPEN trades          │
│   - LONG: price≥TP→WIN, price≤SL→LOSS  │
│   - SHORT: price≤TP→WIN, price≥SL→LOSS │
│   - Записує в БД, рахує в /kpi         │
└────────────────────────────────────────┘
```

---

## 🔧 КОНФІГУРАЦІЯ

### Environment Variables:
```bash
# Telegram
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=xxx

# OpenRouter
OPENROUTER_KEYS=key1,key2
OPENROUTER_MODEL=deepseek/deepseek-chat
OPENROUTER_BASE=https://openrouter.ai/api/v1
OPENROUTER_TIMEOUT=30

# Database
DB_PATH=storage/bot.db

# Trading
MONITORED_SYMBOLS=BTCUSDT,ETHUSDT,BNBUSDT
AUTOPOST_INTERVAL_SEC=300

# Indicators
INDICATOR_ATR_MIN=0.004
INDICATOR_RSI_LONG_MIN=50
INDICATOR_ADX_MIN=18
```

### CFG dict (core_config.py):
```python
CFG = {
    "or_model": "deepseek/deepseek-chat",
    "or_base": "https://openrouter.ai/api/v1",
    "or_timeout": 30,
    "or_slots": [...],
    "analyze_timeframe": "15m",
    "analyze_limit": 150,
    "default_locale": "uk",
    "tz": "Europe/Kyiv",
}
```

---

## 📈 SCALPING MODE (Фіксовані %)

### Особливості:
1. **Фіксований SL%** — не залежить від ATR
2. **Фіксований TP%** — не залежить від resistance
3. **Slippage adjustment** — враховується при розрахунку RR
4. **Gate logic** — 12 індикаторних перевірок
5. **indicators_json** — всі індикатори зберігаються в БД

### Типовий RR при дефолтних налаштуваннях:
```
SL = 0.3%, TP = 1.2%
RR_raw = 1.2/0.3 = 4.0
RR_adj ≈ 2.95 (з урахуванням slippage 0.08%)
```

---

## 📊 LONG/SHORT RATIO (SENTIMENT)

### Огляд:
**Long/Short Ratio** — показник ринкового сентименту з Binance Futures API, який відображає співвідношення лонг та шорт позицій трейдерів.

### API Endpoints (market_data/long_short_ratio.py):
```python
# Всі трейдери
GET /futures/data/globalLongShortAccountRatio
    ?symbol=BTCUSDT&period=5m

# Топ-трейдери  
GET /futures/data/topLongShortAccountRatio
    ?symbol=BTCUSDT&period=5m

# Open Interest
GET /fapi/v1/openInterest
    ?symbol=BTCUSDT
```

### Інтерпретація:
| Ratio | Значення | Сигнал |
|-------|----------|--------|
| > 1.5 | 🟢 Багато лонгів | Можливий short squeeze |
| 0.7 - 1.5 | 🟡 Баланс | Нейтрально |
| < 0.7 | 🔴 Багато шортів | Можливий long squeeze |

### Використання в боті:

#### 1. Команди `/ls` та `/sentiment`:
```
📊 L/S Ratio: BTCUSDT

🌐 Всі трейдери:
  • Longs: 52.3%
  • Shorts: 47.7%
  • Ratio: 1.10

🏆 Топ-трейдери:
  • Longs: 55.1%
  • Shorts: 44.9%
  • Ratio: 1.23

📈 Open Interest: 45,231.5 BTC
```

#### 2. В Autopost повідомленнях:
```
📊 L/S: 🟢 67%/33% (ratio 2.04)
```

#### 3. В Індикаторах (Preset 3):
```
📊 L/S Ratio: 🟢 Long 67% / Short 33% (ratio 2.04)
  ↳ >1.5 багато лонгів (squeeze?), <0.7 багато шортів
```

#### 4. Кнопка в меню символу:
```
[ 🤖 AI BTCUSDT ]  [ 📈 Індикатори BTCUSDT ]
[ 🔗 Залежність BTC/ETH ]  [ 📊 L/S Ratio BTCUSDT ]  ← НОВИЙ
```

### Dataclass:
```python
@dataclass
class LongShortData:
    long_ratio: float       # % лонгів (всі трейдери)
    short_ratio: float      # % шортів (всі трейдери)
    long_short_ratio: float # співвідношення
    top_long_ratio: float   # % лонгів (топ-трейдери)
    top_short_ratio: float  # % шортів (топ-трейдери)
    open_interest: float    # OI в базовій валюті
```

---

## 🐛 ВИПРАВЛЕНІ БАГИ (ця сесія)

1. **Database path mismatch** — `storage/bot.db` тепер пріоритетний
2. **ROLLBACK замість COMMIT** — `get_conn()` тепер робить `commit()` при успіху
3. **trade_mode не передавався** — додано в msg dict у `core.py`
4. **Негативний RR для SHORT** — формула тепер використовує `abs(tp-entry)`
5. **Однаковий RR для всіх scalping** — це by design (фіксовані %)

---

## 🆕 НОВІ ФУНКЦІЇ (остання сесія)

### 1. Long/Short Ratio (Sentiment)
- **Модуль**: `market_data/long_short_ratio.py`
- **Команди**: `/ls`, `/sentiment`
- **Callback**: `ls:{SYMBOL}`
- **Інтеграція**: autopost, індикатори, меню символу

### 2. Оновлена команда /analyze
- Тепер показує символи з `user_settings.monitored_symbols`
- Якщо порожні — fallback на `CFG["default_symbols"]`

### 3. Пояснення до індикаторів
- Кожен індикатор у Preset 3 тепер має `↳` hint
- Пояснення логіки кожного показника

### 4. Оновлений /help
- Структурована довідка з секціями
- Пояснення скальпінгу та L/S Ratio

### 5. Клавіатура
- Додана кнопка `/ls` між `/kpi` та `/orders`
- Додана кнопка `📊 L/S Ratio {SYMBOL}` в меню символу

### 6. 🤖 Покращений AI Trading Plan
- **AI трейди зберігаються в БД** — `trade_mode="ai"`
- **Окремі OPEN ордери для різних джерел** — унікальність OPEN ордерів тепер визначається як `(symbol, timeframe, trade_mode)` (тобто `ai` та `autopost` можуть мати одночасні OPEN ордери на тому ж TF).
- **Відображення**: `/orders` тепер має окрему секцію `🤖 AI` для ордерів, що походять від AI; автопост і standard розділено (scalping лишається в своїй секції).
- **Автозакриття по TP/SL** — кожні 60 секунд
- **Розширений промпт** — детальніший аналіз
- **Нові поля у відповіді**:
  - `tp2` — другий Take Profit рівень
  - `key_levels` — ключові Support/Resistance
  - `signals_bullish` — список бичачих сигналів
  - `signals_bearish` — список ведмежих сигналів

### 7. Формат AI Trading Plan
```
🤖 AI Trading Plan — BTCUSDT
━━━━━━━━━━━━━━━━━━━━

🟢 Напрямок: LONG ↑

📍 Торгові рівні:
   ▸ Entry: $84,000.00
   ▸ Stop Loss: $83,200.00
   ▸ Take Profit 1: $85,500.00
   ▸ Take Profit 2: $86,200.00
   ▸ Risk/Reward: 1:1.88 ⚠️

🔑 Ключові рівні:
   ▸ Support: $83,500.00
   ▸ Resistance: $85,800.00

🟢 Бичачі: RSI oversold, StochRSI cross, MACD rising
🔴 Ведмежі: EMA bearish, Volume below avg

📊 Впевненість: ███████░░░ 70%
⏱ Час утримання: 12h

💡 Аналіз:
_Детальне обґрунтування входу..._

⚠️ NFA. DYOR.
```

### 8. Autopost improvements (остання сесія)
- **Immediate autopost trigger**: при збереженні `monitored_symbols` через панель запускається негайний фоновий прогін автопосту для цього користувача (виконуються warmup/backfill калібрації для нових символів). Це вирішує ситуацію, коли символ додано під час поточного прогону і він не опрацьований до наступного циклу.
- **Detailed gate reasons logging**: при SKIP кандидатів автопосту у лог тепер включені топ‑причини (перші 3 причини) та відсоток gate (напр., `gate=8/14 (57%)`), що полегшує діагностику чому символ не проходить.
- **Як це допоможе**: швидший feedback для нових символів, точніша діагностика відсіву та зменшення timing-related missed posts.

---

## 🧾 KPI (trades) last 7d — поточні результати
```
Symbol    N   Win%  AvgRR   PnL_USD
────────────────────────────────────────
BNBUSDT   16  43.8   1.18      5.03
BTCUSDT   14  50.0   1.39      4.74
ETHUSDT   18  38.9   0.68      2.47
FOGOUSDT  37  35.1   0.63      4.39
LINKUSDT  21  42.9   0.77      3.57
SOLUSDT   26  42.3   0.63      3.46
────────────────────────────────────────
TOTAL     132  40.9   0.88     23.66
────────────────────────────────────────
⚡️ SCALP   130  40.0%           22.71
🤖 AI        2 100.0%            0.96
📉 STD       0   0.0%            0.00

PnL bars:
BNBUSDT  | +############ | +5.03
BTCUSDT  | +###########  | +4.74
FOGOUSDT | +##########   | +4.39
LINKUSDT | +#########    | +3.57
SOLUSDT  | +########     | +3.46
ETHUSDT  | +######       | +2.47
```

_Висновок_: це хороший краткостроковий результат — PnL `+23.66$` при 132 трейдів (скальпінг забезпечує основну частку). Рекомендовано проводити A/B тест порога quality_gate (60% vs 70%) і backtest для оптимізації SL/TP.

---

## 🎛️ АНАЛІЗ КНОПОК І КАРТА ПЕРЕХОДІВ

### 📱 ГОЛОВНА КЛАВІАТУРА (commands.py)

Після `/start` користувач бачить основну клавіатуру з 9 кнопками:

```
[ /ai ]     [ /analyze ]    [ /panel ]
[ /top ]    [ /guide ]      [ /news ]
[ /kpi ]    [ /ls ]         [ /orders ]
```

| Кнопка | Призначення | Перехід |
|--------|-------------|---------|
| `/ai` | AI-аналіз символу з торговим планом | Ввід символу → AI план + збереження в БД |
| `/analyze` | Аналіз моніторинг-пар користувача | Плитка індикаторів для кожного символу |
| `/panel` | Панель налаштувань користувача | Inline-клавіатура з 20+ кнопками |
| `/top` | Топ-20 USDT пар за обсягом/зростанням | Список символів + меню вибору дії |
| `/guide` | Гайд по індикаторах | Статичний текст з поясненнями |
| `/news` | Крипто-новини | Пошук новин (опціонально з query) |
| `/kpi` | Статистика трейдів | KPI: winrate, P&L, RR середній |
| `/ls` | Long/Short Ratio (сентимент) | Аналіз настроїв ринку для символу |
| `/orders` | Відкриті ордери | Список OPEN трейдів з деталями |

### ⚙️ ПАНЕЛЬ НАЛАШТУВАНЬ (/panel)

Inline-клавіатура з динамічними кнопками (panel.py). Стан кнопок залежить від user_settings.

#### Основні перемикачі:
| Кнопка | Призначення | Значення |
|--------|-------------|----------|
| `Autopost: ✅ ON/❌ OFF` | Фоновий моніторинг пар | toggle_autopost: 0/1 |
| `⚙️ Neutral` | Налаштування Neutral mode | Перехід до neutral панелі |
| `📊 KPI` | Швидкий перегляд KPI | Виклик /kpi |
| `📦 Orders` | Відкриті ордери | Виклик /orders |

#### Скальпінг секція:
| Кнопка | Призначення | Опції |
|--------|-------------|--------|
| `⚡ Scalping: ✅ ON/❌ OFF` | Режим фіксованих % | toggle_scalping: 0/1 |
| `SL 0.2%/0.3%/0.5%/0.7%` | Stop Loss % | set_scalp_sl: 0.2-0.7 |
| `TP 0.5%/0.9%/1.2%/1.5%` | Take Profit % | set_scalp_tp: 0.5-1.5 |
| `Slip 0.02%/0.05%/0.08%/0.1%` | Slippage % | set_slippage: 0.02-0.1 |

#### Символи та таймфрейми:
| Кнопка | Призначення | Опції |
|--------|-------------|--------|
| `📋 Symbols: default/list` | Користувацькі символи | edit_symbols (conversation) |
| `🕐 TF:` | Таймфрейм для /ai, /req | 5m, 15m, 1h, 4h, 1d |
| `📡 AP TF:` | Таймфрейм автопосту | 5m, 15m, 1h, 4h, 1d |

#### Автопост налаштування:
| Кнопка | Призначення | Опції |
|--------|-------------|--------|
| `AP RR 1.0/1.5/2.0/3.0` | Мінімальний RR для автопосту | set_ap_rr: 1.0-3.0 |
| `QG 50%/60%/70%/80%` | Quality Gate % | set_quality_gate: 50-80 |

#### Модель та локаль:
| Кнопка | Призначення | Опції |
|--------|-------------|--------|
| `auto/model1/model2/...` | Вибір AI моделі | set_model: auto або конкретна |
| `UK/EN` | Мова відповідей | set_locale: uk/en |

#### Трекери:
| Кнопка | Призначення | Значення |
|--------|-------------|----------|
| `Daily: ✅ ON/❌ OFF` | Щоденний P&L | toggle_daily: 0/1 |
| `Winrate: ✅ ON/❌ OFF` | Тижневий winrate | toggle_winrate: 0/1 |

#### Допомога:
| Кнопка | Призначення |
|--------|-------------|
| `ℹ️ Help` | Довідка по панелі | Статичний текст з поясненнями |

### 🎯 МЕНЮ СИМВОЛУ (/top → вибір символу)

Після вибору символу з `/top` показується меню дій:

```
[ 🤖 AI {SYMBOL} ]        [ 📈 Індикатори {SYMBOL} ]
[ 🔗 Залежність BTC/ETH ]  [ 📊 L/S Ratio {SYMBOL} ]
```

| Кнопка | Призначення | Перехід |
|--------|-------------|---------|
| `🤖 AI {SYMBOL}` | AI-аналіз вибраного символу | /ai {SYMBOL} |
| `📈 Індикатори {SYMBOL}` | Повний звіт індикаторів | format_ta_report() |
| `🔗 Залежність BTC/ETH` | Кореляція з BTC/ETH | /req {SYMBOL} |
| `📊 L/S Ratio {SYMBOL}` | Сентимент ринку | /ls {SYMBOL} |

### ⚖️ NEUTRAL MODE ПАНЕЛЬ (/neutral або ⚙️ Neutral)

Окрема панель для налаштування поведінки при NEUTRAL сигналах:

```
[   CLOSE ]  [   TRAIL ]  [   IGNORE ]
```

| Кнопка | Призначення |
|--------|-------------|
| `CLOSE` | Закривати позицію при NEUTRAL |
| `TRAIL` | Підтягувати SL до BE (-0.25R) |
| `IGNORE` | Нічого не робити (не рекомендовано) |

### 🗺️ КАРТА ПЕРЕХОДІВ

```
┌─────────────┐
│   /start    │
└──────┬──────┘
       │
       ▼
┌─────────────┐     ┌─────────────┐
│Головна кл. │ ──► │   /panel    │
│9 кнопок    │     └──────┬──────┘
└──────┬──────┘            │
       │                   ▼
       ▼            ┌─────────────┐
┌─────────────┐     │Панель налашт│
│   /top      │     │20+ кнопок  │
└──────┬──────┘     └──────┬──────┘
       │                   │
       ▼                   │
┌─────────────┐            │
│Меню символу│            │
│4 кнопки    │            │
└──────┬──────┘            │
       │                   │
       └───────────────────┘
               │
               ▼
       ┌─────────────┐
       │Neutral панель│
       │3 кнопки     │
       └─────────────┘
```

### 🔄 CALLBACK МАРШРУТИЗАЦІЯ

Всі кнопки використовують callback_data патерни:

| Префікс | Обробник | Файл |
|---------|----------|------|
| `panel:` | `on_cb_panel` | panel_handlers.py |
| `neutral_mode:` | `on_cb_neutral` | panel_neutral.py |
| `sym:` | `on_cb_sym` | top_handlers.py |
| `topmode:` | `on_cb_topmode` | top_handlers.py |
| `ai:` | `on_cb_ai` | ai_commands.py |
| `indic:` | `on_cb_indicators` | ai_commands.py |
| `dep:` | `on_cb_dep` | ai_commands.py |
| `ls:` | `on_cb_ls` | handlers_addons.py |

### 📊 CTA (Call-To-Action) ПОТОК

Типовий користувацький шлях:
1. `/start` → CAPTCHA → Головна клавіатура
2. `/panel` → Налаштування autopost/symbols/scalping
3. `/top` → Вибір символу → Меню дій → AI аналіз
4. Автопост відправляє сигнали автоматично

---

## 📝 КОМАНДИ БОТА

| Команда | Опис |
|---------|------|
| `/start` | Привітання + клавіатура |
| `/help` | Довідка (оновлена структура з поясненнями) |
| `/guide` | Гайд по індикаторах |
| `/ping` | Перевірка + модель |
| `/ai <SYMBOL> [TF]` | AI-аналіз з планом (зберігає в БД) |
| `/req <SYMBOL> [TF]` | Кореляція з BTC/ETH |
| `/analyze` | Плитка монів (з user_settings.monitored_symbols) |
| `/top` | Топ-20 пар |
| `/news [query]` | Новини |
| `/panel` | Панель налаштувань |
| `/kpi` | Статистика трейдів |
| `/ls [SYMBOL]` | **🆕** Long/Short Ratio (сентимент ринку) |
| `/sentiment [SYMBOL]` | **🆕** Alias для /ls (повний аналіз) |
| `/orders` | Відкриті ордери |
| `/neutral` | Налаштування Neutral mode (CLOSE/TRAIL/IGNORE) |
| `/daily_now` | Примусовий запуск daily tracker |
| `/winrate_now` | Примусовий запуск winrate tracker |
| `/autopost_now` | Примусовий запуск автопосту |

### Клавіатура команд:
```
[ /ai ]  [ /analyze ]  [ /panel ]
[ /top ]   [ /guide ]   [ /news ]
[ /kpi ]   [ /ls ]   [ /orders ]   ← /ls НОВИЙ
```

---

## � СИСТЕМА БЕЗПЕКИ

### CAPTCHA для нових користувачів
- **Тип**: Emoji-based captcha
- **Активація**: Автоматично при `/start` для нових користувачів
- **Категорії**: Фрукти, тварини, транспорт, погода, спорт, їжа (6 категорій)
- **Декої**: +3 випадкових emoji для плутанини
- **Зберігання**: SQLite таблиця `user_verified`
- **Мета**: Захист від ботів та спаму

**Процес верифікації:**
1. Користувач натискає `/start`
2. Бот перевіряє `user_verified` таблицю
3. Якщо не верифікований → відправляє captcha
4. Користувач обирає правильну категорію emoji
5. При успіху → запис в БД + головна клавіатура

---

## �🔮 РЕКОМЕНДАЦІЇ НА МАЙБУТНЄ

1. **Real trading integration** — Binance Futures API
2. **Multi-user isolation** — окрема БД для кожного user
3. **Backtesting module** — історичне тестування стратегій
4. **ML model** — заміна rule-based gate logic на ML
5. **Risk management** — position sizing, max drawdown limits
6. **Telegram alerts** — push notifications для важливих подій
7. **Web dashboard** — графічний інтерфейс статистики
8. **L/S Ratio в Gate Logic** — додати як 13-й критерій
9. **Funding Rate** — інтеграція funding rate з Binance Futures
10. **Liquidation Data** — відстеження великих ліквідацій

---

## 📊 QUICK REFERENCE

### Формула RR:
```python
# LONG
RR = (TP - Entry) / (Entry - SL)

# SHORT (з abs для правильного знаку)
RR = abs(TP - Entry) / abs(Entry - SL)

# або універсально
RR = abs(TP - Entry) / abs(Entry - SL)
```

### Gate Score:
```python
gate_pct = gate_score / gate_total  # e.g., 8/12 = 0.67
if gate_pct >= GATE_THRESHOLD:      # 0.67 >= 0.4
    open_trade()
```

### P&L:
```python
# LONG
PnL_gross = (close - entry) * qty
# SHORT  
PnL_gross = (entry - close) * qty
# Net
PnL_net = PnL_gross - fees
PnL_pct = (PnL_net / size_usd) * 100
```

---

> **MAXPILOT** — це живий документ, який може оновлюватися разом з проектом.
> 
> **Останнє оновлення**: 
> - **🎛️ Додано повний аналіз кнопок і карту переходів** — детальна документація всіх кнопок, їх призначення та навігації
> - **🏗️ Оновлено архітектуру** — додано всі директорії (trader, stats, signal_tools, tools, scripts, data, logs, storage, alerts, migrations)
> - **📊 Виправлено gate logic** — 13 критеріїв замість 12, оновлені умови
> - **📝 Доповнено таблицю команд** — додано відсутні команди (/neutral, /daily_now, /winrate_now, /autopost_now)
> - **📋 Розширено опис проекту** — додано метрики, особливості, можливості
> - Покращений AI Trading Plan з детальним аналізом (TP2, key_levels, bullish/bearish signals)
> - AI трейди зберігаються в БД (trade_mode="ai") та автоматично закриваються по TP/SL
> - Додано Long/Short Ratio (Sentiment) — повна інтеграція з Binance Futures API
