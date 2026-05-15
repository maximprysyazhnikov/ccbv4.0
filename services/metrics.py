"""Simple metrics wrapper (Prometheus if available, otherwise no-op counters)."""
from __future__ import annotations
import logging

log = logging.getLogger("metrics")

try:
    from prometheus_client import Counter
    AUTOPST_OPENS = Counter("autopost_opens_total", "Autopost opens")
    AUTOPST_SKIPS = Counter("autopost_skips_total", "Autopost skips")
    AUTOPST_FAILS = Counter("autopost_fails_total", "Autopost failures")
    AUTOPST_VOLUME_ERR = Counter("autopost_volume_errors_total", "Autopost volume errors")

    def inc_open():
        AUTOPST_OPENS.inc()

    def inc_skip():
        AUTOPST_SKIPS.inc()

    def inc_fail():
        AUTOPST_FAILS.inc()

    def inc_volume_error():
        AUTOPST_VOLUME_ERR.inc()

except Exception:
    # No prometheus available — use no-op with logs
    def inc_open():
        log.debug("metric: inc_open")

    def inc_skip():
        log.debug("metric: inc_skip")

    def inc_fail():
        log.debug("metric: inc_skip")

    def inc_volume_error():
        log.debug("metric: inc_volume_error")
