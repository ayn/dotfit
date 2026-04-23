"""Utilities for inspecting FIT file contents."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Minimum manual laps to consider an activity as having intervals.
# 1-2 manual laps could just be pausing; 3+ strongly suggests intervals.
MIN_MANUAL_LAPS_FOR_INTERVALS = 3


def has_intervals(fit_path: Path) -> bool:
    """Check if a FIT file contains structured workout or interval data.

    Detection methods (any match → True):
    1. workout_step records — structured workout pushed from Garmin Connect
    2. Multiple manual lap triggers — user pressed lap button at each interval
    """
    if not fit_path.exists():
        return False

    try:
        from fitparse import FitFile

        fitfile = FitFile(str(fit_path))

        # Check for structured workout steps
        for _ in fitfile.get_messages("workout_step"):
            return True

        # Check for manual lap-button intervals
        manual_laps = 0
        for lap in fitfile.get_messages("lap"):
            fields = {d.name: d.value for d in lap.fields}
            if fields.get("lap_trigger") == "manual":
                manual_laps += 1

        if manual_laps >= MIN_MANUAL_LAPS_FOR_INTERVALS:
            return True

    except ImportError:
        logger.debug("fitparse not installed — skipping interval detection")
    except Exception as e:
        logger.debug("Failed to parse FIT file %s: %s", fit_path, e)

    return False
