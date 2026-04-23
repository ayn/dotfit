"""Persistent state tracking for activity download/upload progress."""

import json
import logging
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ActivityRecord(BaseModel):
    garmin_activity_id: int
    activity_name: str = ""
    activity_type: str = ""
    event_type: str = ""
    start_time: str = ""
    file_path: str | None = None
    status: str = "pending"  # pending | downloaded | uploaded | duplicate | failed
    strava_upload_id: int | None = None
    strava_activity_id: int | None = None
    uploaded_at: str | None = None
    error: str | None = None


class StateManager:
    def __init__(self, archive_dir: Path):
        self.path = Path(archive_dir) / ".state.json"
        self.records: dict[str, ActivityRecord] = {}
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                self.records = {k: ActivityRecord(**v) for k, v in data.items()}
            except (json.JSONDecodeError, Exception) as e:
                logger.warning("Failed to load state file, starting fresh: %s", e)
                self.records = {}

    def save(self) -> None:
        """Atomic write: write to tmp file then rename."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {k: v.model_dump() for k, v in self.records.items()},
                indent=2,
                default=str,
            )
        )
        tmp.rename(self.path)

    def _key(self, garmin_id: int) -> str:
        return str(garmin_id)

    def get(self, garmin_id: int) -> ActivityRecord | None:
        return self.records.get(self._key(garmin_id))

    def upsert(self, record: ActivityRecord) -> None:
        key = self._key(record.garmin_activity_id)
        existing = self.records.get(key)
        if existing:
            merged = existing.model_dump()
            # Only update fields that were explicitly passed to the constructor
            for field in record.model_fields_set:
                merged[field] = getattr(record, field)
            self.records[key] = ActivityRecord(**merged)
        else:
            self.records[key] = record

    def mark_downloaded(
        self,
        garmin_id: int,
        file_path: str,
        name: str = "",
        activity_type: str = "",
        event_type: str = "",
        start_time: str = "",
    ) -> None:
        self.upsert(
            ActivityRecord(
                garmin_activity_id=garmin_id,
                activity_name=name,
                activity_type=activity_type,
                event_type=event_type,
                start_time=start_time,
                file_path=file_path,
                status="downloaded",
            )
        )

    def mark_uploaded(
        self, garmin_id: int, upload_id: int | None, strava_id: int | None
    ) -> None:
        from datetime import datetime, timezone

        rec = self.get(garmin_id)
        if rec:
            rec.status = "uploaded"
            rec.strava_upload_id = upload_id
            rec.strava_activity_id = strava_id
            rec.uploaded_at = datetime.now(timezone.utc).isoformat()
            rec.error = None

    def mark_duplicate(self, garmin_id: int, strava_id: int | None = None) -> None:
        rec = self.get(garmin_id)
        if rec:
            rec.status = "duplicate"
            rec.strava_activity_id = strava_id
            rec.error = None

    def mark_failed(self, garmin_id: int, error: str) -> None:
        rec = self.get(garmin_id)
        if rec:
            rec.status = "failed"
            rec.error = error

    def pending_uploads(self) -> list[ActivityRecord]:
        return [r for r in self.records.values() if r.status == "downloaded"]

    def failed_uploads(self) -> list[ActivityRecord]:
        return [r for r in self.records.values() if r.status == "failed"]

    def downloaded_ids(self) -> set[int]:
        return {
            r.garmin_activity_id
            for r in self.records.values()
            if r.status in ("downloaded", "uploaded", "duplicate")
        }

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.records.values():
            counts[r.status] = counts.get(r.status, 0) + 1
        return counts
