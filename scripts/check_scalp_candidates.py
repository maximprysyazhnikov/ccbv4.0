"""Швидка перевірка кандидатов для скальпінгу
Запуск: python scripts/check_scalp_candidates.py
Виводить для кожного символа: чи згенерувався сигнал, gate%, RR, і чому пропущено (якщо пропущено).
"""
import asyncio
import logging
import os
import sys
from collections import Counter
# ensure project root on sys.path
sys.path.insert(0, str((__file__).replace('\\scripts\\check_scalp_candidates.py','')))  # quick hack for local execution
from utils.user_settings import get_user_settings

logging.getLogger("services.scalping_sources").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

async def main():
    from services.scalping_sources import collect_scalping_candidates
    from services.autopost.core import _gate_ok, _load_profit_guard

    user_id = os.getenv("TELEGRAM_CHAT_ID", "default")
    us = get_user_settings(user_id) if user_id else {}
    quality_gate_pct = float(us.get("quality_gate_pct") or 70)
    profit_guard = _load_profit_guard(us if isinstance(us, dict) else {}, "scalping")
    scalping_tp = float(us.get("scalping_tp_pct") or 1.2) if isinstance(us, dict) else 1.2
    scalping_sl = float(us.get("scalping_sl_pct") or 0.3) if isinstance(us, dict) else 0.3
    slippage = float(us.get("slippage_pct") or 0.08) if isinstance(us, dict) else 0.08
    effective_reward = scalping_tp - 2 * slippage
    effective_risk = scalping_sl + 2 * slippage
    rr_min = (effective_reward / effective_risk * 0.95) if effective_risk > 0 else 1.0

    candidates = await collect_scalping_candidates(
        sl_pct=scalping_sl,
        tp_pct=scalping_tp,
        slippage_pct=slippage,
        timeframe="5m",
    )
    if not candidates:
        print("Нема кандидатів для скальпінгу (порожній список)")
        return

    rows = []
    for c in candidates:
        symbol = c.get("symbol")
        direction = str(c.get("direction", "")).upper()
        gate_score = c.get("gate_score")
        gate_total = c.get("gate_total")
        gate_pct = c.get("gate_pct")
        rr_adj = c.get("rr_adj") or c.get("rr_target") or c.get("rr_raw")

        effective_gate_pct = quality_gate_pct
        if profit_guard["enabled"]:
            effective_gate_pct = max(effective_gate_pct, profit_guard["min_gate_pct"])
            if direction == "SHORT":
                effective_gate_pct += profit_guard["short_extra_gate_pct"]
            elif c.get("long_momentum_mode"):
                try:
                    effective_gate_pct = min(
                        effective_gate_pct,
                        float(c.get("long_momentum_gate_pct") or effective_gate_pct),
                    )
                except Exception:
                    pass

        gate_ok, gate_reason = _gate_ok(c, rr_adj, effective_gate_pct)
        hard_blockers = c.get("hard_blockers") or (c.get("ind") or {}).get("hard_blockers") or []
        hard_ok = len(hard_blockers) == 0

        ind = c.get("ind") or {}
        pg_reasons = []
        if profit_guard["enabled"]:
            adx_val = float(ind.get("adx14") or 0.0)
            vol_ratio_val = float(ind.get("vol_ratio") or 0.0)
            if adx_val < profit_guard["min_adx"]:
                pg_reasons.append(f"ADX {adx_val:.1f}<{profit_guard['min_adx']:.1f}")
            if vol_ratio_val < profit_guard["min_vol_ratio"]:
                pg_reasons.append(f"Vol {vol_ratio_val:.2f}<{profit_guard['min_vol_ratio']:.2f}")
            if direction == "SHORT":
                short_rr_min = rr_min + profit_guard["short_rr_bonus"]
                if (rr_adj or 0.0) < short_rr_min:
                    pg_reasons.append(f"SHORT RR {(rr_adj or 0.0):.2f}<{short_rr_min:.2f}")
        pg_ok = not pg_reasons

        final_ok = gate_ok and hard_ok and pg_ok
        rows.append(
            {
                "symbol": symbol,
                "direction": direction,
                "gate": f"{gate_score}/{gate_total} ({gate_pct:.0f}%)",
                "gate_ok": gate_ok,
                "gate_reason": gate_reason,
                "hard_ok": hard_ok,
                "hard": hard_blockers,
                "pg_ok": pg_ok,
                "pg": pg_reasons,
                "final_ok": final_ok,
                "rr": float(rr_adj or 0.0),
                "momentum": bool(c.get("long_momentum_mode")),
            }
        )

    counts = Counter(
        (
            "FINAL_PASS"
            if r["final_ok"]
            else "GATE_SKIP"
            if not r["gate_ok"]
            else "HARD_SKIP"
            if not r["hard_ok"]
            else "PROFIT_GUARD_SKIP"
        )
        for r in rows
    )

    print(f"Загалом кандидатів: {len(rows)}")
    print(
        "Підсумок: "
        f"FINAL_PASS={counts['FINAL_PASS']} | "
        f"GATE_SKIP={counts['GATE_SKIP']} | "
        f"HARD_SKIP={counts['HARD_SKIP']} | "
        f"PROFIT_GUARD_SKIP={counts['PROFIT_GUARD_SKIP']}"
    )
    print()

    for r in rows:
        status = "PASS" if r["final_ok"] else "SKIP"
        if not r["gate_ok"]:
            why = r["gate_reason"]
        elif not r["hard_ok"]:
            why = "hard=" + "; ".join(str(x) for x in r["hard"][:3])
        elif not r["pg_ok"]:
            why = "profit_guard=" + "; ".join(r["pg"])
        else:
            why = "ok"
        momentum = " momentum" if r["momentum"] else ""
        print(
            f"{r['symbol']}: {r['direction']} gate={r['gate']} RR={r['rr']:.2f}"
            f"{momentum} => {status}: {why}"
        )

if __name__ == '__main__':
    asyncio.run(main())
