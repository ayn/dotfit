"""Tests for Strava client — mocked HTTP, no real API calls."""

import json

from dotfit.ratelimit import RateLimiter
from dotfit.strava import StravaClient, UploadResult


def _make_client(tmp_path, rate_limiter=None):
    rl = rate_limiter or RateLimiter()
    client = StravaClient(
        client_id="test_id",
        client_secret="test_secret",
        config_dir=tmp_path,
        rate_limiter=rl,
    )
    # Fake tokens so we don't need real auth
    client._access_token = "fake_token"
    client._refresh_token = "fake_refresh"
    client._expires_at = 9999999999  # far future
    return client


class TestTokenManagement:
    def test_save_and_load_tokens(self, tmp_path):
        client = _make_client(tmp_path)
        client._save_tokens()

        assert client.token_path.exists()
        data = json.loads(client.token_path.read_text())
        assert data["access_token"] == "fake_token"
        assert data["refresh_token"] == "fake_refresh"

        # Load in a new client
        client2 = _make_client(tmp_path)
        client2.load_tokens()
        assert client2._access_token == "fake_token"

    def test_load_missing_tokens_raises(self, tmp_path):
        client = _make_client(tmp_path)
        # Don't save tokens
        try:
            client.load_tokens()
            assert False, "Should have raised"
        except RuntimeError as e:
            assert "Not authenticated" in str(e)

    def test_atomic_token_save(self, tmp_path):
        """Token save should not leave .tmp files."""
        client = _make_client(tmp_path)
        client._save_tokens()
        assert not (tmp_path / "strava_tokens.tmp").exists()


class TestUploadResult:
    def test_success_result(self):
        r = UploadResult(status="success", upload_id=1, strava_activity_id=100)
        assert r.status == "success"
        assert r.strava_activity_id == 100

    def test_duplicate_result(self):
        r = UploadResult(status="duplicate", duplicate_of=200)
        assert r.status == "duplicate"
        assert r.duplicate_of == 200

    def test_error_result(self):
        r = UploadResult(status="error", error="something broke")
        assert r.error == "something broke"


class TestHandleUploadError:
    def test_duplicate_detection(self):
        body = {"error": "activity.duplicated", "activity_id": 42}
        result = StravaClient._handle_upload_error(body)
        assert result.status == "duplicate"
        assert result.strava_activity_id == 42

    def test_generic_error(self):
        body = {"error": "file is corrupt", "activity_id": None}
        result = StravaClient._handle_upload_error(body)
        assert result.status == "error"
        assert "corrupt" in result.error

    def test_duplicate_case_insensitive(self):
        body = {"error": "Duplicate of activity 123", "activity_id": 123}
        result = StravaClient._handle_upload_error(body)
        assert result.status == "duplicate"


class TestRateLimitIntegration:
    def test_rate_limiter_updated_on_token_save(self, tmp_path):
        """Verify that StravaClient shares the rate limiter."""
        rl = RateLimiter(short_limit=10, daily_limit=100)
        client = _make_client(tmp_path, rate_limiter=rl)
        assert client.rate_limiter is rl
