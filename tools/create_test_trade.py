import sqlite3
from datetime import datetime

def create_test_trade(symbol="BTCUSDT", direction="LONG", entry=100, sl=90, tp=110, status="OPEN"):
    conn = sqlite3.connect("storage/bot.db")
    conn.execute(
        """
        INSERT INTO trades (symbol, timeframe, direction, entry, sl, tp, opened_at, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (symbol, "1h", direction, entry, sl, tp, datetime.utcnow().isoformat(), status)
    )
    conn.commit()
    conn.close()
    print("Test trade inserted to storage/bot.db.")

if __name__ == "__main__":
    create_test_trade()
