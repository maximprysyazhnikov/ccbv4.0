# Autopost Hardening Checklist

Date: 2026-01-31
Owner: GitHub Copilot

## Goal
Make autopost/autoclose flow safe, idempotent, observable, and configurable:
- Statuses in autopost messages (OPENED / SKIPPED / CLOSED)
- Per-run and per-day open limits
- Stable-checks (N consecutive passes required)
- Idempotent/atomic openings (DB locks / unique constraint)
- Structured logs & basic metrics
- Alerts for anomalies

## Tasks
1. Add candidate persistence (autopost_candidates) for stable-checks. ✅ Done
2. Require `stable_checks` passes before preparing messages. ✅ Done
3. Add per-run and per-day limits (`max_open_per_run`, `max_open_per_day`) and enforce them at send time. ✅ Done
4. Send explicit status messages after each autopost message (OPENED / ALREADY OPEN / SKIPPED(reason)). ✅ Done
5. Send summary message after autopost run: `Opened: X • Skipped: Y • Closed: Z`. ✅ Done
6. Ensure idempotency and atomic opens: create unique index and use `BEGIN IMMEDIATE` locking. ✅ Done
7. Add skeleton metrics wrapper (Prometheus optional). ✅ Done (no-op if library missing)
8. Add structured status logs for opens/skips. ✅ Done
9. Add alerts (Telegram) for circuit-breaker and repeated errors. ❌ Not started
10. Add unit/integration tests for stable-checks, rate limits, and idempotency. ❌ Not started
11. Add CI job to run smoke e2e for `autopost` in sandbox DB. ❌ Not started

## Notes
- Defaults used: `QG` follows user settings (70% default), `autopost_stable_checks` default=1, `max_open_per_run` default=2, `max_open_per_day` default=6.
- Metrics are available via `services.metrics` and are no-op unless `prometheus_client` is installed.

---

If you want, I can now implement the alerts and tests (items 9–11). Which should I pick next?