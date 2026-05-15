# services/kpi.py
from __future__ import annotations
import os, sqlite3, time

DB_PATH = os.getenv("DB_PATH") or os.getenv("SQLITE_PATH") or os.getenv("DATABASE_PATH") or "storage/bot.db"

def kpi_summary(days: int = 7, table: str = "trades") -> str:
    print(f"[KPI] DB_PATH used: {DB_PATH}")
    con = sqlite3.connect(DB_PATH); cur = con.cursor()
    since = int(time.time()) - days*24*3600
    # гнучкі колонки
    cols = [r[1] for r in cur.execute(f"PRAGMA table_info({table})")]
    rr_col   = "rr_realized" if "rr_realized" in cols else ("rr" if "rr" in cols else None)
    pnl_col  = "pnl_usd" if "pnl_usd" in cols else ("pnl" if "pnl" in cols else None)

    # Use safe SQL expressions when a column is missing
    rr_expr = rr_col if rr_col else '0'
    pnl_expr = pnl_col if pnl_col else '0'
    has_trade_mode = "trade_mode" in cols
    mode_exclude = " AND (trade_mode IS NULL OR LOWER(trade_mode)!='metals_scalping')" if has_trade_mode else ""
    # Якщо closed_at числовий (timestamp), порівнюємо напряму, інакше через strftime
    sample = cur.execute(f"SELECT closed_at FROM {table} WHERE closed_at IS NOT NULL LIMIT 1").fetchone()
    if "closed_at" in cols:
        if sample:
            # Якщо це числовий рядок (наприклад, '1769671281'), порівнюємо як CAST(closed_at AS INTEGER)
            try:
                float(sample[0])
                ts_pred = "CAST(closed_at AS INTEGER) >= ?"
            except Exception:
                ts_pred = "CAST(strftime('%s',closed_at) AS INTEGER)>=?"
        else:
            ts_pred = "CAST(strftime('%s',closed_at) AS INTEGER)>=?"
    else:
        ts_pred = f"{'ts_closed' if 'ts_closed' in cols else 'ts_created'} >= ?"
    q = f"""
      SELECT symbol,
             COUNT(*) AS n,
             ROUND(100.0*SUM(CASE WHEN COALESCE({pnl_expr},0)>0 THEN 1 ELSE 0 END)/COUNT(*),1) AS win_pct,
             ROUND(AVG(COALESCE({rr_expr},0)),2) AS avg_rr,
             ROUND(SUM(COALESCE({pnl_expr},0)),2) AS pnl_usd
      FROM {table}
      WHERE {ts_pred}{mode_exclude}
      GROUP BY symbol
      ORDER BY symbol
    """
    rows = cur.execute(q,(since,)).fetchall()
    
    # ═══════════════════════════════════════════════════════════════════════════
    # SCALPING vs STANDARD breakdown
    # ═══════════════════════════════════════════════════════════════════════════
    scalp_n = scalp_win = 0
    scalp_pnl = 0.0
    std_n = std_win = 0
    std_pnl = 0.0
    
    if has_trade_mode:
        # Scalping stats
        scalp_q = f"""
          SELECT COUNT(*) AS n,
                 SUM(CASE WHEN COALESCE({pnl_expr},0)>0 THEN 1 ELSE 0 END) AS wins,
                 ROUND(SUM(COALESCE({pnl_expr},0)),2) AS pnl
          FROM {table}
          WHERE {ts_pred}{mode_exclude} AND LOWER(trade_mode)='scalping'
        """
        scalp_row = cur.execute(scalp_q, (since,)).fetchone()
        if scalp_row:
            scalp_n = int(scalp_row[0] or 0)
            scalp_win = int(scalp_row[1] or 0)
            scalp_pnl = float(scalp_row[2] or 0.0)
        
        # Standard stats (exclude scalping and ai)
        std_q = f"""
          SELECT COUNT(*) AS n,
                 SUM(CASE WHEN COALESCE({pnl_expr},0)>0 THEN 1 ELSE 0 END) AS wins,
                 ROUND(SUM(COALESCE({pnl_expr},0)),2) AS pnl
          FROM {table}
          WHERE {ts_pred}{mode_exclude} AND (trade_mode IS NULL OR LOWER(trade_mode) NOT IN ('scalping','ai','metals_scalping'))
        """
        std_row = cur.execute(std_q, (since,)).fetchone()
        if std_row:
            std_n = int(std_row[0] or 0)
            std_win = int(std_row[1] or 0)
            std_pnl = float(std_row[2] or 0.0)

        # AI stats
        ai_n = ai_win = 0
        ai_pnl = 0.0
        ai_q = f"""
          SELECT COUNT(*) AS n,
                 SUM(CASE WHEN COALESCE({pnl_expr},0)>0 THEN 1 ELSE 0 END) AS wins,
                 ROUND(SUM(COALESCE({pnl_expr},0)),2) AS pnl
          FROM {table}
          WHERE {ts_pred}{mode_exclude} AND LOWER(trade_mode)='ai'
        """
        ai_row = cur.execute(ai_q, (since,)).fetchone()
        if ai_row:
            ai_n = int(ai_row[0] or 0)
            ai_win = int(ai_row[1] or 0)
            ai_pnl = float(ai_row[2] or 0.0)
    
    con.close()

    head = f"KPI ({table}) last {days}d"
    if not rows:
        return head + "\n— немає даних за період."
    out = [head, "────────────────────────────────────────", "Symbol    N   Win%  AvgRR   PnL_USD", "────────────────────────────────────────"]
    tot_n=tot_win=0; tot_pnl=0.0; rr_acc=0.0; rr_cnt=0
    for s,n,win,avg_rr,pnl in rows:
        out.append(f"{s:8} {int(n):3} {win:5.1f}  {avg_rr:5.2f}  {pnl:8.2f}")
        tot_n += int(n); tot_win += round(win*int(n)/100.0,2); tot_pnl += float(pnl or 0.0)
        if avg_rr is not None: rr_acc += float(avg_rr or 0.0); rr_cnt += 1
    wr_tot = (tot_win / tot_n * 100.0) if tot_n else 0.0
    avg_rr_tot = (rr_acc / rr_cnt) if rr_cnt else 0.0
    out += ["────────────────────────────────────────", f"TOTAL     {tot_n:3} {wr_tot:5.1f}  {avg_rr_tot:5.2f}  {tot_pnl:8.2f}"]
    
    # ═══════════════════════════════════════════════════════════════════════════
    # Add SCALPING / STANDARD summary
    # ═══════════════════════════════════════════════════════════════════════════
    if has_trade_mode and (scalp_n > 0 or std_n > 0 or ( 'ai_n' in locals() and ai_n > 0)):
        scalp_wr = (scalp_win / scalp_n * 100.0) if scalp_n else 0.0
        std_wr = (std_win / std_n * 100.0) if std_n else 0.0
        out.append("────────────────────────────────────────")
        out.append(f"⚡ SCALP   {scalp_n:3} {scalp_wr:5.1f}%        {scalp_pnl:8.2f}")
        # Insert AI line if present
        if 'ai_n' in locals() and ai_n > 0:
            ai_wr = (ai_win / ai_n * 100.0) if ai_n else 0.0
            out.append(f"🤖 AI      {ai_n:3} {ai_wr:5.1f}%        {ai_pnl:8.2f}")
        out.append(f"📉 STD     {std_n:3} {std_wr:5.1f}%        {std_pnl:8.2f}")
    
    return "\n".join(out)
