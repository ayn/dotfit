"""Strava API client: OAuth, upload, rate-limit-aware requests.

Uses httpx directly instead of stravalib to keep dependencies light
and get direct access to rate-limit response headers.
"""

from __future__ import annotations

import json
import logging
import time
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from dotfit.ratelimit import RateLimiter

logger = logging.getLogger(__name__)

STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_API_BASE = "https://www.strava.com/api/v3"


@dataclass
class UploadResult:
    status: str  # success | duplicate | error | rate_limited
    upload_id: int | None = None
    strava_activity_id: int | None = None
    error: str | None = None
    duplicate_of: int | None = None


@dataclass
class StravaClient:
    client_id: str
    client_secret: str
    config_dir: Path
    rate_limiter: RateLimiter
    callback_port: int = 8189

    _access_token: str = field(default="", repr=False)
    _refresh_token: str = field(default="", repr=False)
    _expires_at: float = 0

    @property
    def token_path(self) -> Path:
        return self.config_dir / "strava_tokens.json"

    # ── OAuth ─────────────────────────────────────────────────────────

    def authorize(self) -> None:
        """Run the full OAuth2 authorization code flow with a local callback server."""
        redirect_uri = f"http://localhost:{self.callback_port}/callback"
        auth_url = (
            f"{STRAVA_AUTH_URL}?"
            f"client_id={self.client_id}&"
            f"redirect_uri={redirect_uri}&"
            f"response_type=code&"
            f"scope=activity:write,activity:read_all&"
            f"approval_prompt=auto"
        )
        webbrowser.open(auth_url)
        code = self._wait_for_callback()
        if not code:
            raise RuntimeError("Did not receive authorization code from Strava")
        self._exchange_code(code)

    def _wait_for_callback(self) -> str | None:
        result: dict[str, str | None] = {"code": None}

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                query = parse_qs(urlparse(self.path).query)
                result["code"] = query.get("code", [None])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<html><body><h2>Authorization successful!</h2>"
                    b"<p>You can close this tab and return to the terminal.</p>"
                    b"</body></html>"
                )

            def log_message(self, format, *args):
                pass  # suppress default HTTP log noise

        with HTTPServer(("localhost", self.callback_port), _Handler) as server:
            server.timeout = 120
            server.handle_request()

        return result["code"]

    def _exchange_code(self, code: str) -> None:
        resp = httpx.post(
            STRAVA_TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "grant_type": "authorization_code",
            },
            timeout=30,
        )
        resp.raise_for_status()
        self._apply_token_response(resp.json())

    # ── Token management ──────────────────────────────────────────────

    def load_tokens(self) -> None:
        """Load tokens from disk and refresh if expired."""
        if not self.token_path.exists():
            raise RuntimeError("Not authenticated with Strava. Run: dotfit auth strava")
        data = json.loads(self.token_path.read_text())
        self._access_token = data["access_token"]
        self._refresh_token = data["refresh_token"]
        self._expires_at = data["expires_at"]
        if time.time() >= self._expires_at - 60:
            self._refresh()

    def _refresh(self) -> None:
        logger.info("Refreshing Strava access token")
        resp = httpx.post(
            STRAVA_TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
            },
            timeout=30,
        )
        resp.raise_for_status()
        self._apply_token_response(resp.json())

    def _apply_token_response(self, data: dict) -> None:
        self._access_token = data["access_token"]
        self._refresh_token = data["refresh_token"]
        self._expires_at = data["expires_at"]
        self._save_tokens()

    def _save_tokens(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "access_token": self._access_token,
            "refresh_token": self._refresh_token,
            "expires_at": self._expires_at,
        }
        tmp = self.token_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.rename(self.token_path)

    def _auth_headers(self) -> dict[str, str]:
        if time.time() >= self._expires_at - 60:
            self._refresh()
        return {"Authorization": f"Bearer {self._access_token}"}

    # ── Upload ────────────────────────────────────────────────────────

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        reraise=True,
    )
    def _post_upload(
        self,
        fit_path: Path,
        name: str | None = None,
        activity_type: str | None = None,
        external_id: str | None = None,
    ) -> httpx.Response:
        data: dict[str, str] = {"data_type": "fit"}
        if name:
            data["name"] = name
        if activity_type:
            data["activity_type"] = activity_type
        if external_id:
            data["external_id"] = external_id

        with open(fit_path, "rb") as f:
            resp = httpx.post(
                f"{STRAVA_API_BASE}/uploads",
                headers=self._auth_headers(),
                data=data,
                files={"file": (fit_path.name, f, "application/octet-stream")},
                timeout=60,
            )

        self.rate_limiter.update_from_headers(dict(resp.headers))
        return resp

    def upload_activity(
        self,
        fit_path: Path,
        name: str | None = None,
        activity_type: str | None = None,
        external_id: str | None = None,
        poll_interval: float = 2.0,
        max_polls: int = 30,
    ) -> UploadResult:
        """Upload a FIT file and wait for Strava to process it."""
        resp = self._post_upload(fit_path, name, activity_type, external_id)

        if resp.status_code == 429:
            self.rate_limiter.update_from_headers(dict(resp.headers))
            return UploadResult(status="rate_limited", error="Rate limited by Strava")

        if resp.status_code not in (200, 201):
            return UploadResult(status="error", error=f"HTTP {resp.status_code}: {resp.text}")

        body = resp.json()

        # Check for immediate duplicate detection
        if body.get("error"):
            return self._handle_upload_error(body)

        upload_id = body.get("id")
        if not upload_id:
            return UploadResult(status="error", error="No upload ID in response")

        # Poll until processed
        for _ in range(max_polls):
            time.sleep(poll_interval)
            status_body = self._get_upload_status(upload_id)

            if status_body.get("activity_id"):
                return UploadResult(
                    status="success",
                    upload_id=upload_id,
                    strava_activity_id=status_body["activity_id"],
                )

            if status_body.get("error"):
                return self._handle_upload_error(status_body)

        return UploadResult(
            status="error",
            upload_id=upload_id,
            error="Upload processing timed out after polling",
        )

    def _get_upload_status(self, upload_id: int) -> dict:
        resp = httpx.get(
            f"{STRAVA_API_BASE}/uploads/{upload_id}",
            headers=self._auth_headers(),
            timeout=30,
        )
        self.rate_limiter.update_from_headers(dict(resp.headers))
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _handle_upload_error(body: dict) -> UploadResult:
        error = body.get("error", "")
        activity_id = body.get("activity_id")

        if "duplicate" in str(error).lower():
            return UploadResult(
                status="duplicate",
                strava_activity_id=activity_id,
                duplicate_of=activity_id,
            )
        return UploadResult(
            status="error",
            strava_activity_id=activity_id,
            error=str(error),
        )

    # ── Activity listing ────────────────────────────────────────────────

    def list_activities(
        self, after: float | None = None, per_page: int = 200
    ) -> list[dict]:
        """List the authenticated user's activities.

        Args:
            after: Unix epoch timestamp — only return activities after this time.
            per_page: Page size (max 200).

        Returns list of Strava SummaryActivity dicts.
        """
        all_activities: list[dict] = []
        page = 1
        while True:
            params: dict[str, int] = {"page": page, "per_page": per_page}
            if after is not None:
                params["after"] = int(after)

            resp = httpx.get(
                f"{STRAVA_API_BASE}/athlete/activities",
                headers=self._auth_headers(),
                params=params,
                timeout=30,
            )
            self.rate_limiter.update_from_headers(dict(resp.headers))
            resp.raise_for_status()

            batch = resp.json()
            if not batch:
                break
            all_activities.extend(batch)
            if len(batch) < per_page:
                break
            page += 1

        return all_activities

    # ── Activity update (for workout type) ────────────────────────────

    def update_activity(self, activity_id: int, **kwargs) -> dict:
        """Update a Strava activity (e.g., set workout_type)."""
        resp = httpx.put(
            f"{STRAVA_API_BASE}/activities/{activity_id}",
            headers=self._auth_headers(),
            json=kwargs,
            timeout=30,
        )
        self.rate_limiter.update_from_headers(dict(resp.headers))
        resp.raise_for_status()
        return resp.json()
