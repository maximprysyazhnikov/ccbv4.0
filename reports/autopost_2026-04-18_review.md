# Autopost Review - 2026-04-18

Snapshot time: 2026-04-18 09:20 Europe/Kyiv.

## Open Positions

Current prices were read through the bot's own market data sources: Binance for crypto and `market_data.metals` for metals.

| ID | Symbol | Dir | Mode | Entry | Current | Live RR | Live PnL | Decision |
|---:|---|---|---|---:|---:|---:|---:|---|
| 4234 | BNBUSDT | LONG | scalping | 640.1000 | 644.6500 | +1.37 | +0.51 | Hold, protect profit. Move SL to break-even or slightly above entry. |
| 4262 | XAUUSD | SHORT | metals_scalping | 4876.1001 | 4849.3999 | +1.56 | +0.35 | Hold only with tightened SL. Do not let it return to a full loss. |
| 4272 | XPDUSD | SHORT | metals_scalping | 1584.5000 | 1574.0000 | +1.89 | +0.46 | Hold with locked profit. This is one of the better open positions. |
| 4274 | XAGUSD | SHORT | metals_scalping | 81.6050 | 80.9300 | +2.36 | +0.63 | Very close to TP. Hold to TP or close/protect manually. |
| 4275 | TRXUSDT | LONG | scalping | 0.3271 | 0.3270 | -0.06 | -0.23 | Hold only while original SL remains valid; no add. |
| 4276 | XPTUSD | SHORT | metals_scalping | 2118.6001 | 2114.3999 | +0.57 | -0.00 | Weak profit after fees. Tighten SL to entry or close flat. |
| 4286 | BTCUSDT | LONG | scalping | 77190.6000 | 77128.4200 | -0.16 | -0.28 | Hold, but no reason to add. Let SL/TP logic handle it. |
| 4290 | DOGEUSDT | SHORT | scalping | 0.09929 | 0.09871 | +1.12 | +0.38 | Existing SHORT is profitable; tighten SL to break-even. No new SHORTs. |
| 4291 | TONUSDT | SHORT | scalping | 1.4030 | 1.3950 | +1.10 | +0.37 | Existing SHORT is profitable; tighten SL to break-even. No new SHORTs. |
| 4293 | SOLUSDT | SHORT | scalping | 88.4000 | 88.4400 | -0.09 | -0.25 | Weak open SHORT. Prefer close on next favorable tick or keep only with strict SL. |

Recommended operating stance:

- Do not open new SHORTs for now.
- For profitable open SHORTs, protect them instead of adding exposure.
- Do not average into any open trade.
- Metals shorts are currently profitable, but metals performance over the reviewed window is weak, so keep them on a short leash.

## Runtime Change

Implemented explicit config:

```env
AUTOPOST_DISABLE_SHORTS=true
```

This replaces the temporary `PROFIT_GUARD_SHORT_EXTRA_GATE_PCT=100` / `PROFIT_GUARD_SHORT_RR_BONUS=99` hack.

Runtime `settings` now contains:

```text
autopost_disable_shorts=true
profit_guard_short_extra_gate_pct=5
profit_guard_short_rr_bonus=0.10
```

The switch is enforced in:

- `services/autopost/core.py`
- `services/metals_autopost.py`

## Last 106 Closed Trades

Reviewed trades: `id >= 4178`, excluding still-open positions.

Summary:

| Metric | Value |
|---|---:|
| Closed trades | 106 |
| Wins | 24 |
| Losses | 82 |
| Winrate | 22.6% |
| Total PnL | -22.46 |

By direction:

| Direction | Trades | Wins | WR | PnL |
|---|---:|---:|---:|---:|
| LONG | 90 | 24 | 26.7% | -12.61 |
| SHORT | 16 | 0 | 0.0% | -9.84 |

By mode:

| Mode | Trades | Wins | WR | PnL |
|---|---:|---:|---:|---:|
| scalping | 78 | 19 | 24.4% | -13.39 |
| metals_scalping | 28 | 5 | 17.9% | -9.06 |

Worst symbols:

| Symbol | Trades | Wins | PnL |
|---|---:|---:|---:|
| ADAUSDT | 8 | 0 | -5.56 |
| XPDUSD | 7 | 0 | -4.17 |
| NEARUSDT | 2 | 0 | -2.89 |
| LINKUSDT | 4 | 0 | -2.70 |
| DOGEUSDT | 7 | 1 | -2.24 |
| XPTUSD | 9 | 2 | -2.08 |
| XAGUSD | 8 | 2 | -2.05 |
| DOTUSDT | 3 | 0 | -2.03 |

Best symbols:

| Symbol | Trades | Wins | PnL |
|---|---:|---:|---:|
| XLMUSDT | 3 | 2 | +2.29 |
| OPUSDT | 1 | 1 | +1.76 |
| BTCUSDT | 4 | 2 | +1.57 |
| ARBUSDT | 1 | 1 | +1.42 |
| APTUSDT | 7 | 3 | +1.05 |

## Filter Findings

Gate score did not predict profitability:

| Gate bucket | Trades | Wins | WR | PnL |
|---|---:|---:|---:|---:|
| 70% | 29 | 7 | 24.1% | -4.37 |
| 80% | 49 | 11 | 22.4% | -9.11 |
| 90% | 22 | 4 | 18.2% | -8.66 |
| 100% | 6 | 2 | 33.3% | -0.31 |

Key misses:

1. SHORT entries were the clearest failure.
   The closed SHORT sample was `0/16`, `-9.84`. This is enough to disable new SHORTs until the logic is reworked.

2. Higher gate scores were not safer.
   The 90% bucket had worse PnL than the 70% bucket. The gate is scoring conditions, but not enough market regime quality.

3. VWAP stretch still allowed too many bad entries.
   `vwap_delta_pct > 0.30` appeared in 44 trades: 32 losses, 12 wins, total `-5.64`.

4. ADX was not the main leak.
   `adx < 22` appeared in only 7 trades. Six were losses, but the total damage was `-2.66`, not the core problem.

5. Volume filters were not the main leak in the opened sample.
   The analyzed opened trades did not show `vol_ratio < 0.90`, so raising only volume thresholds will not fix the strategy.

6. Metals need their own throttle.
   `metals_scalping` was 5/28 with `-9.06`. Current metals open trades are green, but the mode as a whole is weak over this window.

## Recommended Next Changes

1. Keep `AUTOPOST_DISABLE_SHORTS=true` for at least the next observation window.
2. Add a low-winrate circuit breaker that stops opening new trades when runtime `low_wr` is active.
3. Add a per-symbol cooldown/ban list for symbols with 0 wins and large negative PnL, starting with `ADAUSDT`, `XPDUSD`, `LINKUSDT`, `NEARUSDT`, `DOTUSDT`.
4. Rework gate scoring so high `gate_pct` cannot pass when market regime is noisy or stretched.
5. Add a separate `METALS_AUTOPOST_OPEN_TRADES=false` or stricter metals gate until metals winrate recovers.
