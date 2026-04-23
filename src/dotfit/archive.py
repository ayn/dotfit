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
