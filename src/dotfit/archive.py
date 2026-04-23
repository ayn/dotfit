"""Archive directory layout: <base>/activities/YYYY/MM/<activity_id>.fit + .json."""

from pathlib import Path


class Archive:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.activities_dir = self.base_dir / "activities"

    def _year_month(self, start_time: str) -> tuple[str, str]:
        """Extract YYYY and MM from a Garmin startTimeLocal string."""
        # Format: "2024-03-15 07:30:00" or similar
        if start_time and len(start_time) >= 7:
            parts = start_time[:10].split("-")
            if len(parts) >= 2:
                return parts[0], parts[1]
        return "unknown", "00"

    def fit_path(self, activity_id: int, start_time: str = "") -> Path:
        year, month = self._year_month(start_time)
        return self.activities_dir / year / month / f"{activity_id}.fit"

    def metadata_path(self, activity_id: int, start_time: str = "") -> Path:
        year, month = self._year_month(start_time)
        return self.activities_dir / year / month / f"{activity_id}.json"

    def has_fit(self, activity_id: int, start_time: str = "") -> bool:
        return self.fit_path(activity_id, start_time).exists()

    def ensure_dir(self, activity_id: int, start_time: str = "") -> None:
        self.fit_path(activity_id, start_time).parent.mkdir(parents=True, exist_ok=True)

    def strava_path(self, start_time: str, source: str, ext: str = "fit") -> Path:
        """Path for files downloaded from Strava: YYYYMMDD-HHMMSS_source.ext"""
        year, month = self._year_month(start_time)
        ts = self._compact_time(start_time)
        return self.activities_dir / year / month / f"{ts}_{source}.{ext}"

    @staticmethod
    def _compact_time(start_time: str) -> str:
        """Convert '2024-03-12T07:15:00Z' or '2024-03-12 07:15:00' to '20240312-071500'."""
        clean = start_time.replace("T", " ").replace("Z", "")[:19]
        parts = clean.split(" ")
        date_part = parts[0].replace("-", "") if parts else "00000000"
        time_part = parts[1].replace(":", "") if len(parts) > 1 else "000000"
        return f"{date_part}-{time_part}"
