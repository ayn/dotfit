"""Tests for archive directory layout."""

from dotfit.archive import Archive


def test_fit_path_with_date(tmp_path):
    archive = Archive(tmp_path)
    path = archive.fit_path(123456, "2024-03-15 07:30:00")
    assert path == tmp_path / "activities" / "2024" / "03" / "123456.fit"


def test_metadata_path(tmp_path):
    archive = Archive(tmp_path)
    path = archive.metadata_path(123456, "2024-03-15 07:30:00")
    assert path == tmp_path / "activities" / "2024" / "03" / "123456.json"


def test_fit_path_unknown_date(tmp_path):
    archive = Archive(tmp_path)
    path = archive.fit_path(123456, "")
    assert path == tmp_path / "activities" / "unknown" / "00" / "123456.fit"


def test_has_fit_false(tmp_path):
    archive = Archive(tmp_path)
    assert not archive.has_fit(999, "2024-01-01 00:00:00")


def test_has_fit_true(tmp_path):
    archive = Archive(tmp_path)
    path = archive.fit_path(999, "2024-01-01 00:00:00")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"fake fit data")
    assert archive.has_fit(999, "2024-01-01 00:00:00")


def test_ensure_dir(tmp_path):
    archive = Archive(tmp_path)
    archive.ensure_dir(42, "2024-06-15 12:00:00")
    assert (tmp_path / "activities" / "2024" / "06").is_dir()


def test_different_months_different_dirs(tmp_path):
    archive = Archive(tmp_path)
    jan = archive.fit_path(1, "2024-01-15 08:00:00")
    dec = archive.fit_path(2, "2024-12-25 10:00:00")
    assert jan.parent != dec.parent
    assert "01" in str(jan)
    assert "12" in str(dec)


def test_strava_path(tmp_path):
    archive = Archive(tmp_path)
    path = archive.strava_path("2024-03-12T07:15:00Z", "coros", "fit")
    assert path == tmp_path / "activities" / "2024" / "03" / "20240312-071500_coros.fit"


def test_strava_path_gpx(tmp_path):
    archive = Archive(tmp_path)
    path = archive.strava_path("2024-06-01 08:30:00", "strava", "gpx")
    assert path == tmp_path / "activities" / "2024" / "06" / "20240601-083000_strava.gpx"


def test_compact_time():
    assert Archive._compact_time("2024-03-12T07:15:00Z") == "20240312-071500"
    assert Archive._compact_time("2024-03-12 07:15:00") == "20240312-071500"
    assert Archive._compact_time("2024-06-01T08:30:45Z") == "20240601-083045"
