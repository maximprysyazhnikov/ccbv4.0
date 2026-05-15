-- Migration: make OPEN-trade uniqueness include trade_mode (allow ai and autopost OPEN trades to coexist)
-- Run: sqlite3 storage/bot.db < migrations/006_trade_mode_unique_index.sql

PRAGMA foreign_keys=OFF;
BEGIN;

-- Drop legacy index that prevented multiple OPEN trades per (symbol,timeframe)
DROP INDEX IF EXISTS uniq_trades_open;

-- Create new unique index scoped by trade_mode
CREATE UNIQUE INDEX IF NOT EXISTS uniq_trades_open_mode ON trades(symbol, timeframe, trade_mode) WHERE status='OPEN';

COMMIT;
PRAGMA foreign_keys=ON;
