"""Tests for FIT file workout detection."""

from pathlib import Path

from dotfit.fit_utils import has_intervals


def _write_fake_fit(path: Path, content: bytes) -> Path:
    """Write bytes to a file for testing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


class TestHasIntervals:
    def test_returns_false_for_nonexistent_file(self, tmp_path):
        assert has_intervals(tmp_path / "nope.fit") is False

    def test_returns_false_for_empty_file(self, tmp_path):
        path = _write_fake_fit(tmp_path / "empty.fit", b"")
        assert has_intervals(path) is False

    def test_returns_false_for_corrupt_file(self, tmp_path):
        path = _write_fake_fit(tmp_path / "junk.fit", b"this is not a FIT file")
        assert has_intervals(path) is False
