"""Tests for Strava cookie-based file download."""

from unittest.mock import MagicMock, patch

import httpx

from dotfit.strava_download import (
    SessionExpiredError,
    StravaDownloader,
    _parse_extension,
    _try_browser_cookie,
)


class TestParseExtension:
    def test_fit_from_content_disposition(self):
        headers = {"content-disposition": 'attachment; filename="activity.fit"'}
        assert _parse_extension(headers) == "fit"

    def test_gpx_from_content_disposition(self):
        headers = {"content-disposition": 'attachment; filename="route.gpx"'}
        assert _parse_extension(headers) == "gpx"

    def test_tcx_from_content_disposition(self):
        headers = {"content-disposition": 'attachment; filename="workout.tcx"'}
        assert _parse_extension(headers) == "tcx"

    def test_default_when_no_headers(self):
        assert _parse_extension({}) == "fit"

    def test_from_content_type_fit(self):
        assert _parse_extension({"content-type": "application/fit"}) == "fit"

    def test_from_content_type_xml(self):
        assert _parse_extension({"content-type": "application/xml"}) == "tcx"


class TestSessionExpiredDetection:
    def test_redirect_to_login(self, tmp_path):
        downloader = StravaDownloader("fake_cookie")

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 302
        mock_response.headers = {"location": "https://www.strava.com/login"}

        with patch("dotfit.strava_download.httpx.get", return_value=mock_response):
            try:
                downloader._fetch(12345)
                assert False, "Should have raised SessionExpiredError"
            except SessionExpiredError as e:
                assert "expired" in str(e).lower()

    def test_html_response_instead_of_binary(self, tmp_path):
        downloader = StravaDownloader("fake_cookie")

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html; charset=utf-8"}

        with patch("dotfit.strava_download.httpx.get", return_value=mock_response):
            try:
                downloader._fetch(12345)
                assert False, "Should have raised SessionExpiredError"
            except SessionExpiredError as e:
                assert "HTML" in str(e)


class TestDownloadOriginal:
    def test_successful_download(self, tmp_path):
        downloader = StravaDownloader("valid_cookie", download_delay=0)

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.headers = {
            "content-disposition": 'attachment; filename="activity.fit"',
            "content-type": "application/octet-stream",
        }
        mock_response.content = b"fake FIT data"

        with patch.object(downloader, "_fetch", return_value=mock_response):
            path, ext = downloader.download_original(
                activity_id=42,
                dest_dir=tmp_path,
                filename_stem="20240312-071500_coros",
            )

        assert ext == "fit"
        assert path.name == "20240312-071500_coros.fit"
        assert path.read_bytes() == b"fake FIT data"

    def test_gpx_download(self, tmp_path):
        downloader = StravaDownloader("valid_cookie", download_delay=0)

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.headers = {
            "content-disposition": 'attachment; filename="route.gpx"',
            "content-type": "application/gpx+xml",
        }
        mock_response.content = b"<gpx>...</gpx>"

        with patch.object(downloader, "_fetch", return_value=mock_response):
            path, ext = downloader.download_original(
                activity_id=42,
                dest_dir=tmp_path,
                filename_stem="20240601-080000_strava",
            )

        assert ext == "gpx"
        assert path.name == "20240601-080000_strava.gpx"


class TestTestCookie:
    def test_valid_cookie(self):
        downloader = StravaDownloader("good_cookie")
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("dotfit.strava_download.httpx.get", return_value=mock_resp):
            assert downloader.test_cookie() is True

    def test_expired_cookie(self):
        downloader = StravaDownloader("bad_cookie")
        mock_resp = MagicMock()
        mock_resp.status_code = 302

        with patch("dotfit.strava_download.httpx.get", return_value=mock_resp):
            assert downloader.test_cookie() is False

    def test_network_error(self):
        downloader = StravaDownloader("cookie")
        with patch(
            "dotfit.strava_download.httpx.get",
            side_effect=httpx.ConnectError("fail"),
        ):
            assert downloader.test_cookie() is False


class TestBrowserCookieFallback:
    def test_no_browser_cookie3_installed(self):
        """When browser-cookie3 is not installed, return None gracefully."""
        with patch.dict("sys.modules", {"browser_cookie3": None}):
            result = _try_browser_cookie()
            # Should return None, not raise
            assert result is None or isinstance(result, str)
