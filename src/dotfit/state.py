"""Persistent state tracking for activity download/upload progress.

State v2 schema:
{
  "version": 2,
  "meta": {"last_strava_pull": "2024-03-20T12:00:00Z"},
  "activities": {
    "g123456": {...},   // keyed by garmin id
    "s987654": {...}    // keyed by strava id (no garmin equivalent)
  }
}

Keys use source-prefixed IDs to avoid collisions:
  g{garmin_id}  — activities originating from Garmin
  s{strava_id}  — activities only known from Strava
"""

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)

VERSION = 2


class ActivityRecord(BaseModel):
    start_time: str = ""
    garmin_id: int | None = None
    strava_id: int | None = None
    strava_external_id: str | None = None
    source: str = "unknown"  # garmin | coros | strava | wahoo | zwift | manual | unknown
    file_path: str | None = None
    file_ext: str = "fit"
    activity_name: str = ""
    activity_type: str = ""
    event_type: str = ""
    uploaded_to_strava: bool = False
    downloaded_from: str | None = None  # garmin | strava
    strava_upload_id: int | None = None
    error: str | None = None


class StateManager:
    def __init__(self, archive_dir: Path):
        self.path = Path(archive_dir) / ".state.json"
        self.records: dict[str, ActivityRecord] = {}
        self._meta: dict = {"last_strava_pull": None}
        self._garmin_idx: dict[int, str] = {}
        self._strava_idx: dict[int, str] = {}
        self.load()

    # ── Persistence ───────────────────────────────────────────────────

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Failed to read state file: %s", e)
            return

        if isinstance(data, dict) and data.get("version") == VERSION:
            self._load_v2(data)
        elif isinstance(data, dict) and "version" not in data:
            self._migrate_v1(data)
        else:
            logger.warning("Unknown state file version, starting fresh")

        self._rebuild_indexes()

    def _load_v2(self, data: dict) -> None:
        self._meta = data.get("meta", {"last_strava_pull": None})
        for k, v in data.get("activities", {}).items():
            try:
                self.records[k] = ActivityRecord(**v)
            except Exception as e:
                logger.warning("Skipping invalid record %s: %s", k, e)

    def _migrate_v1(self, old_data: dict) -> None:
        """Migrate v1 state (flat dict keyed by garmin_id) to v2."""
        backup = self.path.with_suffix(".json.bak")
        if not backup.exists():
            shutil.copy2(self.path, backup)
            logger.info("Backed up v1 state to %s", backup)

        for garmin_id_str, rec_data in old_data.items():
            try:
                gid = rec_data.get("garmin_activity_id")
                if gid is None:
                    gid = int(garmin_id_str)
            except (ValueError, TypeError):
                continue

            old_status = rec_data.get("status", "")
            uploaded = old_status in ("uploaded", "duplicate")

            self.records[f"g{gid}"] = ActivityRecord(
                start_time=rec_data.get("start_time", ""),
                garmin_id=gid,
                strava_id=rec_data.get("strava_activity_id"),
                source="garmin",
                file_path=rec_data.get("file_path"),
                file_ext="fit",
                activity_name=rec_data.get("activity_name", ""),
                activity_type=rec_data.get("activity_type", ""),
                event_type=rec_data.get("event_type", ""),
                uploaded_to_strava=uploaded,
                downloaded_from="garmin",
                strava_upload_id=rec_data.get("strava_upload_id"),
                error=rec_data.get("error") if old_status == "failed" else None,
            )

    def _rebuild_indexes(self) -> None:
        self._garmin_idx.clear()
        self._strava_idx.clear()
        for key, rec in self.records.items():
            if rec.garmin_id is not None:
                self._garmin_idx[rec.garmin_id] = key
            if rec.strava_id is not None:
                self._strava_idx[rec.strava_id] = key

    def save(self) -> None:
        """Atomic write: write to tmp file then rename."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": VERSION,
            "meta": self._meta,
            "activities": {k: v.model_dump() for k, v in self.records.items()},
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str))
        tmp.rename(self.path)

    # ── Lookups ───────────────────────────────────────────────────────

    def get_by_garmin_id(self, garmin_id: int) -> ActivityRecord | None:
        key = self._garmin_idx.get(garmin_id)
        return self.records.get(key) if key else None

    def get_by_strava_id(self, strava_id: int) -> ActivityRecord | None:
        key = self._strava_idx.get(strava_id)
        return self.records.get(key) if key else None

    def find_by_start_time(
        self, start_time: str, tolerance_seconds: int = 30
    ) -> tuple[ActivityRecord | None, str | None]:
        """Find a record with start_time within ±tolerance. Returns (record, key)."""
        target = _parse_time(start_time)
        if not target:
            return None, None
        for key, rec in self.records.items():
            rec_time = _parse_time(rec.start_time)
            if rec_time and abs((target - rec_time).total_seconds()) <= tolerance_seconds:
                return rec, key
        return None, None

    # ── Garmin operations ─────────────────────────────────────────────

    def mark_downloaded_from_garmin(
        self,
        garmin_id: int,
        file_path: str,
        name: str = "",
        activity_type: str = "",
        event_type: str = "",
        start_time: str = "",
    ) -> None:
        key = f"g{garmin_id}"
        if key in self.records:
            rec = self.records[key]
            rec.file_path = file_path
            rec.downloaded_from = "garmin"
            if name:
                rec.activity_name = name
            if activity_type:
                rec.activity_type = activity_type
            if event_type:
                rec.event_type = event_type
            if start_time:
                rec.start_time = start_time
        else:
            rec = ActivityRecord(
                garmin_id=garmin_id,
                start_time=start_time,
                source="garmin",
                file_path=file_path,
                activity_name=name,
                activity_type=activity_type,
                event_type=event_type,
                downloaded_from="garmin",
            )
            self.records[key] = rec
            self._garmin_idx[garmin_id] = key

    def mark_uploaded_to_strava(
        self, garmin_id: int, upload_id: int | None, strava_id: int | None
    ) -> None:
        key = self._garmin_idx.get(garmin_id)
        if not key or key not in self.records:
            return
        rec = self.records[key]
        rec.uploaded_to_strava = True
        rec.strava_upload_id = upload_id
        rec.error = None
        if strava_id:
            rec.strava_id = strava_id
            self._strava_idx[strava_id] = key

    def mark_duplicate(
        self, *, garmin_id: int | None = None, strava_id: int | None = None
    ) -> None:
        key = self._resolve_key(garmin_id=garmin_id, strava_id=strava_id)
        if key and key in self.records:
            rec = self.records[key]
            rec.uploaded_to_strava = True
            rec.error = None
            if strava_id and rec.strava_id is None:
                rec.strava_id = strava_id
                self._strava_idx[strava_id] = key

    def mark_failed(
        self, error: str, *, garmin_id: int | None = None, strava_id: int | None = None
    ) -> None:
        key = self._resolve_key(garmin_id=garmin_id, strava_id=strava_id)
        if key and key in self.records:
            self.records[key].error = error

    # ── Strava pull operations ────────────────────────────────────────

    def mark_downloaded_from_strava(
        self,
        strava_id: int,
        file_path: str,
        file_ext: str = "fit",
        name: str = "",
        activity_type: str = "",
        start_time: str = "",
        source: str = "unknown",
        external_id: str | None = None,
    ) -> None:
        key = f"s{strava_id}"
        rec = ActivityRecord(
            strava_id=strava_id,
            strava_external_id=external_id,
            start_time=start_time,
            source=source,
            file_path=file_path,
            file_ext=file_ext,
            activity_name=name,
            activity_type=activity_type,
            downloaded_from="strava",
        )
        self.records[key] = rec
        self._strava_idx[strava_id] = key

    def link_strava_to_existing(
        self, key: str, strava_id: int, external_id: str | None = None
    ) -> None:
        """Link a Strava activity ID to an existing record (e.g., garmin-origin)."""
        if key in self.records:
            rec = self.records[key]
            rec.strava_id = strava_id
            rec.uploaded_to_strava = True
            if external_id:
                rec.strava_external_id = external_id
            self._strava_idx[strava_id] = key

    # ── Queries ───────────────────────────────────────────────────────

    def pending_strava_uploads(self) -> list[ActivityRecord]:
        """Activities downloaded from Garmin but not yet on Strava."""
        return [
            r
            for r in self.records.values()
            if r.downloaded_from == "garmin"
            and not r.uploaded_to_strava
            and r.file_path
            and r.error is None
        ]

    def failed_strava_uploads(self) -> list[ActivityRecord]:
        return [
            r
            for r in self.records.values()
            if r.error is not None
            and r.downloaded_from == "garmin"
            and not r.uploaded_to_strava
        ]

    def counts(self) -> dict[str, int]:
        c = {
            "downloaded_garmin": 0,
            "downloaded_strava": 0,
            "uploaded_to_strava": 0,
            "pending_upload": 0,
            "failed": 0,
            "total": len(self.records),
        }
        for r in self.records.values():
            if r.downloaded_from == "garmin":
                c["downloaded_garmin"] += 1
            if r.downloaded_from == "strava":
                c["downloaded_strava"] += 1
            if r.uploaded_to_strava:
                c["uploaded_to_strava"] += 1
            if r.error:
                c["failed"] += 1
        c["pending_upload"] = len(self.pending_strava_uploads())
        return c

    # ── Meta ──────────────────────────────────────────────────────────

    @property
    def last_strava_pull(self) -> str | None:
        return self._meta.get("last_strava_pull")

    @last_strava_pull.setter
    def last_strava_pull(self, value: str | None) -> None:
        self._meta["last_strava_pull"] = value

    # ── Internal ──────────────────────────────────────────────────────

    def _resolve_key(
        self, *, garmin_id: int | None = None, strava_id: int | None = None
    ) -> str | None:
        if garmin_id is not None:
            key = self._garmin_idx.get(garmin_id)
            if key:
                return key
        if strava_id is not None:
            key = self._strava_idx.get(strava_id)
            if key:
                return key
        return None


def _parse_time(time_str: str) -> datetime | None:
    """Parse various ISO-ish time formats into UTC datetime."""
    if not time_str:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            dt = datetime.strptime(time_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None
