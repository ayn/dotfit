"""Garmin Connect client: authentication, activity listing, FIT download."""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import date, datetime
from pathlib import Path

from garminconnect import Garmin
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


class GarminClient:
    def __init__(self, config_dir: Path):
        self.token_dir = Path(config_dir) / "garmin_tokens"
        self.client: Garmin | None = None

    def login(self, email: str, password: str) -> None:
        """Interactive login with email/password. Handles MFA."""
        self.token_dir.mkdir(parents=True, exist_ok=True)
        self.client = Garmin(email, password)
        # login() with a tokenstore path will save tokens automatically on success
        self.client.login(tokenstore=str(self.token_dir))
        logger.info("Garmin session saved to %s", self.token_dir)

    def resume_session(self) -> bool:
        """Load a previously saved session. Returns False if no saved session."""
        if not self.token_dir.exists():
            return False
        try:
            self.client = Garmin()
            self.client.login(str(self.token_dir))
            return True
        except Exception as e:
            logger.warning("Failed to resume Garmin session: %s", e)
            self.client = None
            return False

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
        reraise=True,
    )
    def get_all_activities(
        self,
        after: date | None = None,
        before: date | None = None,
    ) -> list[dict]:
        """Fetch all activities from Garmin, newest first.

        Activities are returned sorted by start time descending.
        Date filtering is applied client-side for reliability.
        """
        assert self.client is not None, "Not logged in"

        all_activities: list[dict] = []
        start = 0
        batch_size = 100

        while True:
            batch = self.client.get_activities(start, batch_size)
            if not batch:
                break

            for act in batch:
                start_time = act.get("startTimeLocal", "")
                act_date = self._parse_date(start_time)

                if before and act_date and act_date > before:
                    continue
                if after and act_date and act_date < after:
                    # Activities are newest-first; once we're past our cutoff, stop
                    return all_activities

                all_activities.append(act)

            if len(batch) < batch_size:
                break
            start += batch_size

        return all_activities

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
        reraise=True,
    )
    def download_fit(self, activity_id: int, dest: Path) -> Path:
        """Download the original FIT file for an activity.

        Garmin returns a ZIP archive; we extract the .fit from it.
        """
        assert self.client is not None, "Not logged in"

        data = self.client.download_activity(
            activity_id, dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL
        )

        if data is None or len(data) == 0:
            raise ValueError(f"No data returned for activity {activity_id} (manual activity?)")

        dest.parent.mkdir(parents=True, exist_ok=True)

        # Garmin wraps FIT files in a ZIP
        if data[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                fit_files = [f for f in zf.namelist() if f.lower().endswith(".fit")]
                if not fit_files:
                    raise ValueError(f"No .fit file in archive for activity {activity_id}")
                dest.write_bytes(zf.read(fit_files[0]))
        else:
            # Raw FIT bytes
            dest.write_bytes(data)

        return dest

    @staticmethod
    def _parse_date(start_time: str) -> date | None:
        try:
            return datetime.strptime(start_time[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None
