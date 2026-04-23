"""Strava API rate-limit tracker.

Strava includes two rate windows in every response:
  X-RateLimit-Limit:  <15min_limit>,<daily_limit>
  X-RateLimit-Usage:  <15min_usage>,<daily_usage>

This module tracks those values and provides pre-emptive checks
so we can pause or exit before hitting 429s.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, short_limit: int = 90, daily_limit: int = 900):
        # Our conservative thresholds (may be lower than Strava's actual limits)
        self.short_limit = short_limit
        self.daily_limit = daily_limit

        # Actual values from Strava headers
        self.short_usage: int = 0
        self.short_limit_actual: int = 600
        self.daily_usage: int = 0
        self.daily_limit_actual: int = 30000

        # Track when we last saw headers (for 15-min window estimation)
        self._last_update: float = 0

    def update_from_headers(self, headers: dict) -> None:
        """Parse Strava rate-limit headers and update internal counters."""
        limit_header = headers.get("X-RateLimit-Limit", headers.get("x-ratelimit-limit"))
        usage_header = headers.get("X-RateLimit-Usage", headers.get("x-ratelimit-usage"))

        if limit_header:
            try:
                parts = str(limit_header).split(",")
                self.short_limit_actual = int(parts[0].strip())
                self.daily_limit_actual = int(parts[1].strip())
            except (IndexError, ValueError):
                logger.warning("Failed to parse X-RateLimit-Limit: %s", limit_header)

        if usage_header:
            try:
                parts = str(usage_header).split(",")
                self.short_usage = int(parts[0].strip())
                self.daily_usage = int(parts[1].strip())
            except (IndexError, ValueError):
                logger.warning("Failed to parse X-RateLimit-Usage: %s", usage_header)

        self._last_update = time.monotonic()

    def can_proceed(self) -> bool:
        """Check if we're under our conservative thresholds."""
        return self.short_usage < self.short_limit and self.daily_usage < self.daily_limit

    def short_window_exhausted(self) -> bool:
        return self.short_usage >= self.short_limit

    def daily_exhausted(self) -> bool:
        return self.daily_usage >= self.daily_limit

    def wait_time(self) -> float:
        """Seconds to wait for the 15-min window to reset.

        Strava's 15-min window resets at :00, :15, :30, :45.
        We compute time until the next quarter-hour boundary.
        """
        if not self.short_window_exhausted():
            return 0
        now = time.time()
        seconds_into_window = now % 900  # 900s = 15min
        return 900 - seconds_into_window + 5  # +5s buffer

    def status(self) -> str:
        return (
            f"15min: {self.short_usage}/{self.short_limit} "
            f"(actual limit {self.short_limit_actual}), "
            f"daily: {self.daily_usage}/{self.daily_limit} "
            f"(actual limit {self.daily_limit_actual})"
        )
