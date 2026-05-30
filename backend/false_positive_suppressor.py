"""
============================================================
false_positive_suppressor.py — Alert Noise Reduction
Project : Smart Cloud Pulse AI Monitor (AIOps Edition)
------------------------------------------------------------
Prevents alert storms by enforcing two gates:

Gate 1 — Consecutive threshold
  An anomaly must appear in N consecutive polling cycles
  before an alert is dispatched.  A single spike that
  resolves itself (e.g. a 1-second CPU burst) will not fire.

Gate 2 — Cooldown window
  The same server will not trigger another alert within
  ALERT_COOLDOWN_MINUTES, even if anomalies continue.

Both limits are configurable in .env.

Usage:
    from backend.false_positive_suppressor import should_alert

    # Call for every poll result:
    if should_alert(server_id, is_anomaly=True):
        dispatch_alert(...)
============================================================
"""

import time
import logging
import sys
import os
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import ALERT_CONSECUTIVE_MIN, ALERT_COOLDOWN_MINUTES

logger = logging.getLogger(__name__)


class FPSuppressor:
    """
    Stateful false-positive suppressor.

    Internal state per server:
        count           : consecutive anomaly poll count
        last_alert_ts   : Unix timestamp of the last dispatched alert
    """

    def __init__(self, consecutive_min: int = 2, cooldown_minutes: int = 5):
        self._consecutive_min  = max(1, consecutive_min)
        self._cooldown_seconds = max(0, cooldown_minutes) * 60
        # defaultdict so new servers get zero-state automatically
        self._state = defaultdict(lambda: {"count": 0, "last_alert_ts": 0.0})

    def should_alert(self, server_id, is_anomaly: bool) -> bool:
        """
        Evaluate whether an alert should be dispatched.

        Must be called for every polling cycle (both anomaly and normal).
        Normal readings reset the consecutive counter.

        Returns True only when BOTH gates pass.
        """
        sid   = int(server_id or 0)
        state = self._state[sid]

        if is_anomaly:
            state["count"] += 1
        else:
            # Normal reading — reset streak
            state["count"] = 0
            return False

        # Gate 1: consecutive threshold
        if state["count"] < self._consecutive_min:
            logger.debug(
                "FP suppressor: server %s — anomaly streak %d/%d (below threshold)",
                sid, state["count"], self._consecutive_min,
            )
            return False

        # Gate 2: cooldown window
        now     = time.time()
        elapsed = now - state["last_alert_ts"]
        if elapsed < self._cooldown_seconds:
            logger.debug(
                "FP suppressor: server %s — cooldown active (%.0fs remaining)",
                sid, self._cooldown_seconds - elapsed,
            )
            return False

        # Both gates passed — fire alert and record timestamp
        state["last_alert_ts"] = now
        logger.info(
            "FP suppressor: server %s — alert approved (streak=%d, elapsed=%.0fs)",
            sid, state["count"], elapsed,
        )
        return True

    def get_streak(self, server_id) -> int:
        """Return the current consecutive anomaly poll count for a server."""
        return self._state[int(server_id or 0)]["count"]

    def reset(self, server_id) -> None:
        """Manually reset state for a server (e.g. after remediation)."""
        self._state[int(server_id or 0)] = {"count": 0, "last_alert_ts": 0.0}


# ─── Module-level singleton ───────────────────────────────────────────────────
_suppressor = FPSuppressor(
    consecutive_min  = ALERT_CONSECUTIVE_MIN,
    cooldown_minutes = ALERT_COOLDOWN_MINUTES,
)


def should_alert(server_id, is_anomaly: bool) -> bool:
    """Module-level helper — use this in app.py."""
    return _suppressor.should_alert(server_id, is_anomaly)


def get_streak(server_id) -> int:
    """Return consecutive anomaly poll count for a server."""
    return _suppressor.get_streak(server_id)


def reset_server(server_id) -> None:
    """Reset suppressor state for a server."""
    _suppressor.reset(server_id)
