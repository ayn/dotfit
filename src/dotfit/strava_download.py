"""Strava file download via session cookie.

The export_original endpoint is a web endpoint (not part of the documented API),
so it requires a browser session cookie rather than OAuth tokens.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

EXPORT_URL = "https://www.strava.com/activities/{activity_id}/export_original"


class SessionExpiredError(Exception):
    """Raised when the Strava session cookie is expired or invalid."""


class StravaDownloader:
    """Download original activity files from Strava using session cookies."""

    def __init__(self, session_cookie: str, download_delay: float = 1.5):
        self.session_cookie = session_cookie
        self.download_delay = download_delay
        self._last_download: float = 0

    @classmethod
    def create(
        cls, session_cookie: str = "", download_delay: float = 1.5
    ) -> StravaDownloader:
        """Create a downloader, trying browser cookies if no cookie is provided."""
        cookie = session_cookie or _try_browser_cookie()
        if not cookie:
            raise RuntimeError(
                "No Strava session cookie found.\n"
                "Either:\n"
                "  1. Log into strava.com in your browser (Firefox recommended on macOS),\n"
                "     then re-run — browser-cookie3 will read it automatically.\n"
                "  2. Set STRAVA_SESSION_COOKIE in .env (copy from browser devtools).\n"
                "Run: dotfit auth strava-cookie  to verify."
            )
        return cls(cookie, download_delay)

    def download_original(
        self, activity_id: int, dest_dir: Path, filename_stem: str
    ) -> tuple[Path, str]:
        """Download the original file for a Strava activity.

        Returns (file_path, extension).
        """
        self._throttle()
        resp = self._fetch(activity_id)

        # Detect file type from Content-Disposition header
        ext = _parse_extension(resp.headers)

        dest = dest_dir / f"{filename_stem}.{ext}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)

        return dest, ext

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        reraise=True,
    )
    def _fetch(self, activity_id: int) -> httpx.Response:
        resp = httpx.get(
            EXPORT_URL.format(activity_id=activity_id),
            cookies={"_strava4_session": self.session_cookie},
            follow_redirects=False,
            timeout=60,
        )

        # Session expired — redirect to login
        if resp.status_code in (301, 302, 303, 307):
            location = resp.headers.get("location", "")
            if "/login" in location or "/session" in location:
                raise SessionExpiredError(
                    "Strava session expired. Log into strava.com in your browser "
                    "and re-run, or update STRAVA_SESSION_COOKIE in .env."
                )

        # Got HTML instead of binary — also session expiry
        content_type = resp.headers.get("content-type", "")
        if "text/html" in content_type:
            raise SessionExpiredError(
                "Strava returned HTML instead of a file — session likely expired. "
                "Log into strava.com in your browser and re-run."
            )

        if resp.status_code == 429:
            raise httpx.TransportError("Rate limited by Strava (429)")

        resp.raise_for_status()
        return resp

    def _throttle(self) -> None:
        """Sleep between downloads to be polite."""
        elapsed = time.monotonic() - self._last_download
        if elapsed < self.download_delay:
            time.sleep(self.download_delay - elapsed)
        self._last_download = time.monotonic()

    def test_cookie(self) -> bool:
        """Test if the session cookie is valid by hitting the dashboard."""
        try:
            resp = httpx.get(
                "https://www.strava.com/dashboard",
                cookies={"_strava4_session": self.session_cookie},
                follow_redirects=False,
                timeout=15,
            )
            return resp.status_code == 200
        except Exception:
            return False


def _try_browser_cookie() -> str | None:
    """Try to read the Strava session cookie from a local browser."""
    try:
        import browser_cookie3
    except ImportError:
        logger.debug("browser-cookie3 not installed, skipping browser cookie lookup")
        return None

    # Try Firefox first (most reliable on macOS), then Chrome
    for browser_fn in (browser_cookie3.firefox, browser_cookie3.chrome):
        try:
            cj = browser_fn(domain_name=".strava.com")
            for cookie in cj:
                if cookie.name == "_strava4_session":
                    logger.info("Found Strava session cookie via %s", browser_fn.__name__)
                    return cookie.value
        except Exception as e:
            logger.debug("Failed to read cookies via %s: %s", browser_fn.__name__, e)
            continue

    return None


def _parse_extension(headers: httpx.Headers | dict) -> str:
    """Parse file extension from Content-Disposition or Content-Type headers."""
    cd = headers.get("content-disposition", "")
    if "filename=" in cd:
        # Content-Disposition: attachment; filename="activity.fit"
        name = cd.split("filename=")[-1].strip('" ')
        if "." in name:
            return name.rsplit(".", 1)[-1].lower()

    content_type = str(headers.get("content-type", ""))
    if "fit" in content_type:
        return "fit"
    if "tcx" in content_type or "xml" in content_type:
        return "tcx"
    if "gpx" in content_type:
        return "gpx"

    return "fit"  # default
