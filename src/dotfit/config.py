from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    strava_client_id: str = ""
    strava_client_secret: str = ""
    garmin_email: str = ""
    garmin_password: str = ""
    archive_dir: Path = Path("./archive")
    config_dir: Path = Path.home() / ".config" / "dotfit"
    strava_rate_limit_15min: int = 200
    strava_rate_limit_daily: int = 2000
    strava_session_cookie: str = ""
    strava_download_delay: float = 1.5


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
