from utils.db import get_conn

def print_trades():
    with get_conn() as conn:
        rows = conn.execute("SELECT id, symbol, direction, entry, sl, tp, status, close_price, close_reason FROM trades ORDER BY id DESC LIMIT 10").fetchall()
        for row in rows:
            print(row)

if __name__ == "__main__":
    print_trades()
