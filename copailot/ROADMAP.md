# ROADMAP — Autopost Hardening (short-term)

Owner: GitHub Copilot
Created: 2026-01-31

## Objective
Make autopost stable, auditable, and safe to run continuously in production.

## Phases
1. Stabilize (Days 0–1)
   - [x] candidate tracking table + stable-checks
   - [x] per-run/day limits enforcement
   - [x] idempotent openings via DB unique index + immediate lock
   - [x] status messages + run summary
   - [x] basic metrics wrapper and structured logs
2. Monitoring & Alerts (Days 1–2)
   - [ ] Telegram alerts for circuit-breakers and repeated failures
   - [ ] PagerDuty / Email integration (optional)
   - [ ] Configure Prometheus exporter (if prometheus_client available)
3. Testing & CI (Days 2–4)
   - [ ] Unit tests for gate, stable-checks, persistence
   - [ ] Integration smoke test: run_autopost_once with sandbox DB
   - [ ] Add CI job to run smoke tests on PRs
4. Ops & Tuning (Day 4+)
   - [ ] Tune defaults (max opens, stable-checks) after 1 week of telemetry
   - [ ] Add dashboard (Grafana) for metrics
   - [ ] Hardening: isolate user DBs, improve transaction semantics

## Next 3 actions (immediate)
1. Add Telegram alerts and a circuit-breaker to auto-disable opens for 1 hour after N consecutive fails. (I can implement now)
2. Add unit tests for stable-checks and rate limit behavior.
3. Add a short smoke job in CI that runs `python -c "from services.autopost.core import run_autopost_once; ..."` against a sandbox DB (separate workflow file).

---

Tell me which of the next actions to start with (1/2/3) and I will implement it.