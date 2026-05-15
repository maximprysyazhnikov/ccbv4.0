# Trading Bot Roadmap

Updated: 2026-04-18

This roadmap is the working checklist for improving the bot from the current risk-protected state into a transparent, adaptive trading system. We move in small phases. Each phase must include verification before the next one starts.

## Operating Rule

Every implementation step must finish with:

- [ ] Syntax check for changed Python files.
- [ ] Bot restart when runtime code changed.
- [ ] Log check after first scheduler tick.
- [ ] Database/schema check if storage changed.
- [ ] Short written summary of what changed, what was verified, and what remains risky.

Current safety posture:

- [x] SHORT auto-open disabled.
- [x] Low-WR pause enabled.
- [x] Recovery mode added.
- [x] Symbol cooldown after SL enabled.
- [x] Symbol ban rules enabled.
- [x] Metals auto-open disabled.
- [x] `autopost_bridge` reversal-close bug fixed.

## Phase 1: Observability

Goal: stop guessing from raw logs. The bot should explain why it opened, skipped, paused, or limited a candidate.

### 1.1 Decision Log Table

- [x] Create `services/decision_log.py`.
- [x] Add `ensure_decision_log_schema()`.
- [x] Add `log_decision(...)`.
- [x] Store structured fields:
  - `ts`
  - `source`
  - `symbol`
  - `timeframe`
  - `direction`
  - `trade_mode`
  - `gate_score`
  - `gate_total`
  - `gate_pct`
  - `rr`
  - `decision`
  - `reason`
  - `risk_state`
  - `indicators_json`

Expected decisions:

- [x] `PAUSED`
- [ ] `RECOVERY`
- [x] `SHORT_DISABLED`
- [x] `SYMBOL_COOLDOWN`
- [x] `SYMBOL_BAN`
- [x] `GATE_FAIL`
- [x] `HARD_BLOCKERS`
- [x] `PROFIT_GUARD`
- [x] `RR_FAIL`
- [x] `PREPARED`
- [x] `SENT`
- [x] `OPENED`
- [x] `LIMIT_REACHED`
- [x] `ALREADY_OPEN`
- [x] `BRIDGE_SKIP`

Verification:

- [x] Run schema creation on `storage/bot.db`.
- [x] Confirm `decision_log` exists.
- [x] Trigger or wait for one autopost scan.
- [x] Confirm at least one row is inserted.
- [x] Confirm JSON fields are valid.

### 1.2 Wire Decision Logging Into Autopost

- [x] Log `PAUSED` in `services/autopost/core.py`.
- [x] Log `SHORT_DISABLED`.
- [x] Log symbol cooldown/ban decisions.
- [x] Log gate failures.
- [x] Log hard blockers.
- [x] Log profit guard failures.
- [x] Log RR failures.
- [x] Log `PREPARED` when a candidate becomes a Telegram message.

Verification:

- [x] Run `python -m py_compile services\autopost\core.py services\decision_log.py`.
- [x] Restart bot.
- [x] Confirm logs still show scheduler startup.
- [x] Query latest `decision_log` rows.

### 1.3 Wire Decision Logging Into Execution

- [x] Log `SENT` in `main.py autopost_scan()`.
- [x] Log `OPENED`.
- [x] Log `LIMIT_REACHED`.
- [x] Log `ALREADY_OPEN`.
- [x] Log `BRIDGE_SKIP`.
- [x] Log bridge failures with structured reason.

Verification:

- [x] Run `python -m py_compile main.py`.
- [x] Restart bot.
- [x] Confirm no duplicate sends.
- [x] Confirm execution decisions appear in `decision_log`.

## Phase 2: Telegram Visibility

Goal: get clear operational state directly from Telegram.

### 2.1 `/risk`

- [x] Add `/risk` command.
- [x] Show current mode: `PAUSED`, `RECOVERY`, or `NORMAL`.
- [x] Show recent WR window.
- [x] Show pause/recovery thresholds.
- [x] Show `AUTOPOST_DISABLE_SHORTS`.
- [x] Show `METALS_AUTOPOST_OPEN_TRADES`.
- [x] Show open trades count.
- [x] Show daily opens count.
- [x] Show top active risk blocks.

Verification:

- [ ] Run command manually in Telegram.
- [x] Confirm it matches DB state.
- [x] Confirm no token/secret leaks.

### 2.2 `/why SYMBOL`

- [x] Add `/why SYMBOL`.
- [x] Show global mode.
- [x] Show recent symbol stats.
- [x] Show consecutive SL count.
- [x] Show cooldown/ban reason.
- [x] Show whether SHORT-disabled blocks it.
- [x] Show last trade result.

Verification:

- [x] Test `/why ADAUSDT`.
- [x] Test `/why BTCUSDT`.
- [x] Confirm bad symbols explain bans/cooldowns.
- [x] Confirm healthy symbols show no false block.

## Phase 3: Exit Quality

Goal: protect winners better and reduce full givebacks.

### 3.1 Break-Even Protection

- [x] Add config `MOVE_SL_TO_BE_AT_RR=1.0`.
- [x] In `position_manager`, when live RR >= threshold, move SL to entry.
- [x] Avoid repeated updates with `be_done`.

Verification:

- [x] Unit/smoke test RR calculation.
- [x] Confirm an eligible open trade would be updated.
- [x] Confirm non-eligible trades are unchanged.

### 3.2 Profit Lock

- [x] Add config `LOCK_PROFIT_AT_RR=1.5`.
- [x] Add config `LOCK_PROFIT_R=0.3`.
- [x] Move SL to lock +0.3R when RR >= 1.5.

Verification:

- [x] Simulate LONG and SHORT examples.
- [x] Confirm SL only moves in profit direction.

### 3.3 Trailing

- [x] Add config `TRAIL_AFTER_RR=2.0`.
- [x] Use ATR or local swing based trailing.
- [x] Log every trail decision.

Verification:

- [x] Confirm trailing never widens risk.
- [x] Confirm decision is visible in logs and later in `decision_log`.

## Phase 4: Entry Quality

Goal: improve winrate before scaling up volume.

### 4.1 Long-Only Quality Profile

- [x] Enforce stricter LONG profile while shorts are disabled:
  - ADX >= 22
  - vol_ratio >= 0.90
  - VWAP delta <= 0.30
  - BB%B <= 0.80
  - no symbol cooldown
  - no low-WR pause

Verification:

- [x] Compare candidate count before/after.
- [x] Confirm obvious stretched entries are skipped.

### 4.2 Market Regime Filter

- [x] Add regime classification:
  - `TREND_UP`
  - `TREND_DOWN`
  - `RANGE`
  - `CHOP`
  - `LOW_LIQUIDITY`
- [x] Allow LONG only in favorable regimes.

Verification:

- [x] Log regime per prepared candidate.
- [x] Confirm `CHOP` and `LOW_LIQUIDITY` skip.

### 4.3 Expected Value Check

- [x] Compute recent WR and avg R by mode/symbol/direction.
- [x] Estimate EV before opening.
- [x] Skip when EV <= 0.

Verification:

- [x] Backfill EV on recent trades.
- [x] Confirm current weak modes fail EV.

## Phase 5: Reporting

Goal: bot produces useful summaries without manual log archaeology.

### 5.1 Daily Risk Report

- [x] Summarize trades, wins/losses, PnL.
- [x] Show mode state.
- [x] Show top skipped reasons from `decision_log`.
- [x] Show worst/best symbols.
- [x] Show open-position live RR.

Verification:

- [x] Generate report from current DB.
- [x] Confirm numbers match direct SQL.

### 5.2 Decision Report

- [x] Count candidates.
- [x] Count opened/sent/skipped/paused.
- [x] Show top skip reasons.
- [x] Show symbols most often blocked.

Verification:

- [x] Compare report with `decision_log` aggregate SQL.

## Phase 6: Controlled Scaling

Goal: increase risk only after evidence improves.

### 6.1 Recovery Exit Rules

- [x] Leave `PAUSED` only when WR >= 20%.
- [x] Leave `RECOVERY` only when WR >= 35%.
- [x] Restore normal limits gradually.

Verification:

- [x] Simulate WR states with test queries.
- [x] Confirm log says `PAUSED`, `RECOVERY`, or `NORMAL`.

### 6.2 Re-enable Metals Carefully

- [x] Keep `METALS_AUTOPOST_OPEN_TRADES=false` until 7-day paper stats improve.
- [x] Add metals signal-only reporting.
- [x] Re-enable metals only in recovery-limited mode first.

Verification:

- [x] Confirm metals can produce messages without opening trades.

### 6.3 Re-enable SHORTs Only In Paper Mode

- [x] Add `SHORT_SIGNAL_ONLY=true`.
- [x] Log SHORT candidates but do not open.
- [x] Require positive paper stats before auto-open.

Verification:

- [x] Confirm SHORT signal-only code path is enabled.
- [x] Confirm no SHORT trade opens.

## Phase 7: Paper Validation Loop

Goal: keep learning while real auto-open is paused. The bot should record paper-only candidates, track their TP/SL outcome, and report whether disabled streams deserve to be re-enabled.

### 7.1 Paper Signal Store

- [x] Create `services/paper_signals.py`.
- [x] Add `paper_signals` schema.
- [x] Store source, symbol, timeframe, direction, entry, sl, tp, rr, trade_mode, reason, status.
- [x] Avoid duplicate open paper signals per source/symbol/timeframe/direction.

Verification:

- [x] Create schema in `storage/bot.db`.
- [x] Insert/update a smoke paper signal.
- [x] Confirm duplicate prevention works.

### 7.2 Capture Candidates While Paused

- [x] When global low-WR pause triggers, log candidates as paper-only instead of losing them.
- [x] Store SHORT candidates as paper-only while `SHORT_SIGNAL_ONLY=true`.
- [x] Store metals signal-only candidates.
- [x] Do not send Telegram signal/open trade from paper-only capture.

Verification:

- [x] Trigger/wait for autopost scan while `PAUSED`.
- [x] Confirm `paper_signals` gets candidates.
- [x] Confirm no new real trades are opened.

### 7.3 Paper TP/SL Closer

- [x] Add paper closer job.
- [x] Close paper signals when simulated TP or SL is hit.
- [x] Store close_price, close_reason, rr_realized, pnl_r, closed_at.

Verification:

- [x] Simulate LONG TP/SL.
- [x] Simulate SHORT TP/SL.
- [x] Confirm status changes from `OPEN` to `CLOSED`.

### 7.4 `/paper_report`

- [x] Add Telegram command `/paper_report`.
- [x] Show paper count, WR, avg R, PnL R.
- [x] Split by source/mode/direction/symbol.
- [x] Show which disabled streams are improving.

Verification:

- [x] Generate report from current DB.
- [x] Confirm numbers match SQL aggregates.

## Phase 8: Live Risk Hygiene

Goal: make current open-position risk easier to manage before any scaling.

### 8.1 Open Position Risk Table

- [x] Add `/open_risk`.
- [x] Show entry, current SL, TP, live RR where price is available.
- [x] Highlight positions without live price.
- [x] Highlight stale positions.

Verification:

- [x] Run local formatter.
- [x] Confirm no secrets in output.

### 8.2 Settings Consistency Audit

- [x] Add a local audit script/report for critical settings.
- [x] Detect dangerous runtime DB overrides vs `.env`.
- [x] Show recommended values.

Verification:

- [x] Confirm audit catches `9999` style limits if they reappear.

## Phase 9: Paper Quarantine Release Rules

Goal: decide from paper data which disabled streams are healthy enough for cautious recovery.

### 9.1 Release Eligibility Engine

- [x] Add configurable thresholds:
  - `PAPER_RELEASE_MIN_CLOSED=20`
  - `PAPER_RELEASE_MIN_WR=0.35`
  - `PAPER_RELEASE_MIN_PNL_R=0.0`
  - `PAPER_RELEASE_MIN_AVG_R=0.0`
- [x] Compute eligibility by source/mode/direction/symbol.
- [x] Mark streams as `ELIGIBLE`, `WATCH`, or `BLOCKED`.
- [x] Never auto-enable real opening from this engine.

Verification:

- [x] Simulate eligible paper stream.
- [x] Simulate blocked paper stream.
- [x] Confirm live settings do not change.

### 9.2 `/release_report`

- [x] Add Telegram command `/release_report`.
- [x] Show top eligible paper streams.
- [x] Show blocked streams and reasons.
- [x] Show current live safety switches.

Verification:

- [x] Generate report from current DB.
- [x] Confirm report matches paper SQL.
- [x] Confirm no secret values are printed.

### 9.3 Release Audit Log

- [x] Store release evaluations in `decision_log`.
- [x] Include threshold snapshot in reason.
- [x] Keep report read-only.

Verification:

- [x] Confirm `decision_log` has release evaluation rows.
- [x] Confirm no real trade is opened by release evaluation.

## Phase 10: Candidate Allowlist Proposal

Goal: when paper data proves a stream is healthy, generate a proposal for cautious recovery without changing live trading settings.

### 10.1 Proposal Builder

- [x] Build proposal from `ELIGIBLE` release streams only.
- [x] Include source, mode, direction, symbol, WR, closed count, pnlR, avgR.
- [x] Include exact proposed recovery scope.
- [x] Keep proposal read-only.

Verification:

- [x] Simulate eligible stream.
- [x] Confirm proposal includes only eligible stream.
- [x] Confirm no live setting changes.

### 10.2 `/allowlist_proposal`

- [x] Add Telegram command `/allowlist_proposal`.
- [x] Show `No eligible streams yet` while samples are below thresholds.
- [x] Show proposed symbols/directions when eligible exists.
- [x] Log proposal generation in `decision_log`.

Verification:

- [x] Generate report from current DB.
- [x] Confirm audit row is written.
- [x] Confirm no real trade opens.

## Phase 11: Proposal Notification

Goal: notify when an allowlist proposal becomes ready without repeatedly spamming Telegram.

### 11.1 Periodic Proposal Watcher

- [x] Add `ALLOWLIST_PROPOSAL_NOTIFY_ENABLED=true`.
- [x] Add `ALLOWLIST_PROPOSAL_NOTIFY_INTERVAL_SEC=1800`.
- [x] Add `ALLOWLIST_PROPOSAL_LOOKBACK_HOURS=168`.
- [x] Check eligible streams periodically.
- [x] Send Telegram notification only when proposal fingerprint changes.

Verification:

- [x] Confirm no notification when no eligible streams exist.
- [x] Confirm fingerprint is stored in `settings`.
- [x] Confirm no live trading setting changes.

## Phase 12: Performance Epoch Reset

Goal: allow a clean forward-only performance window without deleting old trade history.

### 12.1 Epoch-Based WR

- [x] Add `AUTOPOST_PERF_EPOCH_TS`.
- [x] Make low-WR pause count closed trades only after epoch.
- [x] Make recovery mode count closed trades only after epoch.
- [x] Use `RECOVERY_WARMUP` until the new epoch has enough closed trades.

Verification:

- [x] Set epoch to current time.
- [x] Confirm `/risk` shows reset/warmup state.
- [x] Confirm old trades remain in DB.
- [x] Confirm normal limits are not used during warmup.

### 12.2 `/reset_perf_epoch`

- [x] Add Telegram command `/reset_perf_epoch`.
- [x] Store epoch in runtime settings.
- [x] Log reset in `decision_log`.
- [x] Keep old trade rows untouched.

Verification:

- [x] Compile changed files.
- [x] Restart bot.
- [x] Confirm autopost is no longer blocked by old low WR.
- [x] Confirm recovery caps still apply.

Notes:

- Epoch reset is active from 2026-04-18 12:44:55 Europe/Kyiv.
- Live logs show `perf_epoch warmup: closed 0/20 since reset` instead of old low-WR PAUSED.
- `/risk` shows effective warmup limits: 1 open per run, 2 opens per day.
- Old trade rows remain in DB; current real open trades are still real exposure and are not reset.

## Verification Commands

Use these after relevant changes:

```powershell
python -m py_compile main.py services\autopost\core.py services\metals_autopost.py services\autopost_bridge.py
```

```powershell
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'main\.py' -and $_.Name -match 'python' }
```

```powershell
Get-Content -Tail 120 logs\app.log
```

```powershell
@'
import sqlite3
con = sqlite3.connect('storage/bot.db')
cur = con.cursor()
for row in cur.execute("select name from sqlite_master where type='table' order by name"):
    print(row[0])
'@ | python -
```

## Current Next Step

- [x] Phase 12.2: restart and verify epoch reset live.
- [ ] Let the post-reset loop collect closed real trades; review `/risk`, `/open_risk`, `/release_report`, and `/allowlist_proposal`.
