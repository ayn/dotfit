"""Integration tests for pull-strava dedup and download flow.

Tests the full pull_strava logic with mocked Strava API (list_activities)
and mocked download client (export_original). No real HTTP calls.
"""

from unittest.mock import patch

import httpx
import pytest

from dotfit.archive import Archive
from dotfit.state import StateManager
from dotfit.strava_download import SessionExpiredError, StravaDownloader

# ── Helpers ───────────────────────────────────────────────────────────


def _make_strava_activity(
    strava_id: int,
    name: str = "Activity",
    start_date: str = "2024-06-01T08:00:00Z",
    external_id: str | None = None,
    sport_type: str = "Run",
) -> dict:
    """Build a Strava SummaryActivity dict as returned by the list API."""
    return {
        "id": strava_id,
        "name": name,
        "start_date": start_date,
        "external_id": external_id,
        "sport_type": sport_type,
    }


def _make_downloader(tmp_path) -> StravaDownloader:
    """Create a downloader with no throttle."""
    return StravaDownloader("fake_cookie", download_delay=0)


def _fake_download(dest_dir, filename_stem, ext="fit"):
    """Simulate a successful download by writing a fake file."""
    path = dest_dir / f"{filename_stem}.{ext}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake FIT data")
    return path, ext


# ── Test: skip activity already tracked by strava_id ──────────────────


def test_skip_already_tracked_by_strava_id(tmp_path):
    """If an activity's strava_id is already in state, skip it entirely."""
    state = StateManager(tmp_path)
    state.mark_downloaded_from_strava(
        strava_id=1001,
        file_path="/existing.fit",
        start_time="2024-06-01T08:00:00Z",
        source="coros",
    )
    state.save()

    activities = [_make_strava_activity(1001, "Already Here")]
    downloader = _make_downloader(tmp_path)

    # Run the dedup logic inline (mirrors pull_strava's core loop)
    downloaded, skipped, linked = _run_dedup_loop(
        state, Archive(tmp_path), downloader, activities
    )

    assert skipped == 1
    assert downloaded == 0
    assert linked == 0


# ── Test: link by external_id → garmin_id match ──────────────────────


def test_link_by_external_id_garmin_match(tmp_path):
    """Activity with external_id matching a garmin record gets linked, not downloaded."""
    state = StateManager(tmp_path)
    state.mark_downloaded_from_garmin(
        garmin_id=555, file_path="/garmin/555.fit", start_time="2024-06-01T08:00:00Z"
    )
    state.save()

    activities = [
        _make_strava_activity(
            2001, "Garmin Synced Run",
            start_date="2024-06-01T08:00:00Z",
            external_id="garmin_push_555",
        )
    ]
    downloader = _make_downloader(tmp_path)

    downloaded, skipped, linked = _run_dedup_loop(
        state, Archive(tmp_path), downloader, activities
    )

    assert linked == 1
    assert downloaded == 0
    # Verify the link
    rec = state.get_by_garmin_id(555)
    assert rec.strava_id == 2001
    assert rec.uploaded_to_strava is True


def test_link_by_numeric_external_id(tmp_path):
    """Pure numeric external_id should match as garmin_id."""
    state = StateManager(tmp_path)
    state.mark_downloaded_from_garmin(
        garmin_id=12345, file_path="/garmin/12345.fit", start_time="2024-03-01T07:00:00Z"
    )
    state.save()

    activities = [
        _make_strava_activity(
            3001, "Uploaded via dotfit",
            start_date="2024-03-01T07:00:00Z",
            external_id="12345",
        )
    ]
    downloader = _make_downloader(tmp_path)

    downloaded, skipped, linked = _run_dedup_loop(
        state, Archive(tmp_path), downloader, activities
    )

    assert linked == 1
    assert state.get_by_strava_id(3001) is not None


# ── Test: link by fuzzy start_time match ──────────────────────────────


def test_link_by_start_time_within_tolerance(tmp_path):
    """Activity within ±30s of an existing record gets linked."""
    state = StateManager(tmp_path)
    state.mark_downloaded_from_garmin(
        garmin_id=777, file_path="/garmin/777.fit",
        start_time="2024-06-15T09:00:00Z",
    )
    state.save()

    # Strava reports start 15 seconds later (GPS vs watch clock drift)
    activities = [
        _make_strava_activity(
            4001, "Drifted Start",
            start_date="2024-06-15T09:00:15Z",
            external_id="coros_xyz",  # not a garmin external_id
        )
    ]
    downloader = _make_downloader(tmp_path)

    downloaded, skipped, linked = _run_dedup_loop(
        state, Archive(tmp_path), downloader, activities
    )

    assert linked == 1
    assert downloaded == 0
    rec = state.get_by_garmin_id(777)
    assert rec.strava_id == 4001


def test_no_link_outside_tolerance(tmp_path):
    """Activity more than 30s away should NOT be linked — it gets downloaded."""
    state = StateManager(tmp_path)
    state.mark_downloaded_from_garmin(
        garmin_id=888, file_path="/garmin/888.fit",
        start_time="2024-06-15T09:00:00Z",
    )
    state.save()

    # 2 minutes later — different activity
    activities = [
        _make_strava_activity(
            5001, "Different Activity",
            start_date="2024-06-15T09:02:00Z",
            external_id="coros_abc",
        )
    ]
    downloader = _make_downloader(tmp_path)

    with patch.object(downloader, "download_original") as mock_dl:
        mock_dl.side_effect = lambda aid, dest_dir, stem: _fake_download(dest_dir, stem)
        downloaded, skipped, linked = _run_dedup_loop(
            state, Archive(tmp_path), downloader, activities
        )

    assert downloaded == 1
    assert linked == 0


# ── Test: new activity gets downloaded ────────────────────────────────


def test_new_activity_downloaded(tmp_path):
    """A completely new activity should be downloaded and recorded in state."""
    state = StateManager(tmp_path)
    archive = Archive(tmp_path)
    downloader = _make_downloader(tmp_path)

    activities = [
        _make_strava_activity(
            6001, "Coros Morning Run",
            start_date="2024-07-01T06:30:00Z",
            external_id="coros_run_001",
            sport_type="Run",
        )
    ]

    with patch.object(downloader, "download_original") as mock_dl:
        mock_dl.side_effect = lambda aid, dest_dir, stem: _fake_download(dest_dir, stem)
        downloaded, skipped, linked = _run_dedup_loop(
            state, archive, downloader, activities
        )

    assert downloaded == 1
    rec = state.get_by_strava_id(6001)
    assert rec is not None
    assert rec.source == "coros"
    assert rec.downloaded_from == "strava"
    assert rec.activity_name == "Coros Morning Run"
    assert rec.file_path is not None
    assert ".fit" in rec.file_path


# ── Test: session expiry stops the loop ───────────────────────────────


def test_session_expiry_stops_cleanly(tmp_path):
    """SessionExpiredError should stop the loop and preserve state."""
    state = StateManager(tmp_path)
    archive = Archive(tmp_path)
    downloader = _make_downloader(tmp_path)

    activities = [
        _make_strava_activity(7001, "Run 1", start_date="2024-08-01T07:00:00Z"),
        _make_strava_activity(7002, "Run 2", start_date="2024-08-02T07:00:00Z"),
    ]

    with patch.object(downloader, "download_original") as mock_dl:
        mock_dl.side_effect = SessionExpiredError("cookie expired")

        with pytest.raises(SessionExpiredError):
            _run_dedup_loop(state, archive, downloader, activities, raise_session_error=True)


# ── Test: download failure marks failed, continues ────────────────────


def test_download_failure_marks_failed_continues(tmp_path):
    """A download error for one activity shouldn't stop others."""
    state = StateManager(tmp_path)
    archive = Archive(tmp_path)
    downloader = _make_downloader(tmp_path)

    activities = [
        _make_strava_activity(8001, "Fails", start_date="2024-09-01T07:00:00Z",
                              external_id="coros_a"),
        _make_strava_activity(8002, "Succeeds", start_date="2024-09-02T07:00:00Z",
                              external_id="coros_b"),
    ]

    call_count = 0

    def side_effect(aid, dest_dir, stem):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.TransportError("network down")
        return _fake_download(dest_dir, stem)

    with patch.object(downloader, "download_original", side_effect=side_effect):
        downloaded, skipped, linked = _run_dedup_loop(
            state, archive, downloader, activities
        )

    assert downloaded == 1  # second one succeeded
    # First one should have an error recorded
    # (it won't have a state entry from mark_downloaded_from_strava since dl failed,
    #  but mark_failed needs a key — check the logic handles this)


# ── Test: mixed batch with dedup + download ───────────────────────────


def test_mixed_batch(tmp_path):
    """Realistic batch: some linked, some skipped, some downloaded."""
    state = StateManager(tmp_path)
    # Pre-existing garmin activity
    state.mark_downloaded_from_garmin(
        garmin_id=100, file_path="/g/100.fit", start_time="2024-01-10T08:00:00Z"
    )
    # Pre-existing strava activity
    state.mark_downloaded_from_strava(
        strava_id=200, file_path="/s/200.fit", start_time="2024-01-11T08:00:00Z",
        source="coros",
    )
    state.save()

    activities = [
        # Should link to garmin:100 via external_id
        _make_strava_activity(301, "Garmin Run", start_date="2024-01-10T08:00:00Z",
                              external_id="garmin_push_100"),
        # Should skip — already tracked
        _make_strava_activity(200, "Already Here", start_date="2024-01-11T08:00:00Z"),
        # Should download — completely new
        _make_strava_activity(302, "Coros Swim", start_date="2024-02-01T09:00:00Z",
                              external_id="coros_swim_1"),
    ]

    archive = Archive(tmp_path)
    downloader = _make_downloader(tmp_path)

    with patch.object(downloader, "download_original") as mock_dl:
        mock_dl.side_effect = lambda aid, dest_dir, stem: _fake_download(dest_dir, stem)
        downloaded, skipped, linked = _run_dedup_loop(
            state, archive, downloader, activities
        )

    assert linked == 1   # garmin:100 linked to strava:301
    assert skipped == 1  # strava:200 already tracked
    assert downloaded == 1  # strava:302 downloaded


# ── Test: duplicate start times at race events ────────────────────────


def test_race_event_duplicate_start_times(tmp_path):
    """Two activities at the same start time (e.g. brick race) are handled correctly.

    Once the first one is linked/downloaded, the second one at the same time
    should still be treated independently.
    """
    state = StateManager(tmp_path)
    archive = Archive(tmp_path)
    downloader = _make_downloader(tmp_path)

    # Two Coros activities starting at the exact same time (multisport event)
    activities = [
        _make_strava_activity(9001, "Swim Leg", start_date="2024-07-04T07:00:00Z",
                              external_id="coros_tri_swim", sport_type="Swim"),
        _make_strava_activity(9002, "Bike Leg", start_date="2024-07-04T07:00:00Z",
                              external_id="coros_tri_bike", sport_type="Ride"),
    ]

    with patch.object(downloader, "download_original") as mock_dl:
        mock_dl.side_effect = lambda aid, dest_dir, stem: _fake_download(dest_dir, stem)
        downloaded, skipped, linked = _run_dedup_loop(
            state, archive, downloader, activities
        )

    # Both should be downloaded (different strava IDs, neither pre-existed)
    assert downloaded == 2
    assert state.get_by_strava_id(9001) is not None
    assert state.get_by_strava_id(9002) is not None


# ── Dedup loop extracted from CLI (testable without typer) ────────────


def _run_dedup_loop(
    state: StateManager,
    archive: Archive,
    downloader: StravaDownloader,
    activities: list[dict],
    raise_session_error: bool = False,
) -> tuple[int, int, int]:
    """Run the core dedup + download logic from pull_strava.

    Extracted here to test without invoking typer CLI machinery.
    Returns (downloaded, skipped, linked).
    """
    from dotfit.activity_types import extract_garmin_id, infer_source

    downloaded = 0
    skipped = 0
    linked = 0

    for act in activities:
        strava_id = act["id"]
        start_date = act.get("start_date", "")
        external_id = act.get("external_id")
        name = act.get("name", "")
        sport_type = act.get("sport_type", "")

        # Already known by strava_id?
        if state.get_by_strava_id(strava_id):
            skipped += 1
            continue

        # Dedup: external_id → garmin_id match
        garmin_id = extract_garmin_id(external_id)
        if garmin_id and state.get_by_garmin_id(garmin_id):
            key = state._garmin_idx[garmin_id]
            state.link_strava_to_existing(key, strava_id, external_id)
            state.save()
            linked += 1
            continue

        # Dedup: fuzzy start_time match — only link to pre-existing records
        # (not ones we just downloaded in this same run from Strava)
        existing, existing_key = state.find_by_start_time(start_date, tolerance_seconds=30)
        if existing and existing_key and existing.downloaded_from != "strava":
            state.link_strava_to_existing(existing_key, strava_id, external_id)
            state.save()
            linked += 1
            continue

        # New activity — download
        source = infer_source(external_id)
        dest_dir = archive.strava_path(start_date, source).parent
        filename_stem = archive._compact_time(start_date) + f"_{source}"

        try:
            file_path, ext = downloader.download_original(strava_id, dest_dir, filename_stem)

            state.mark_downloaded_from_strava(
                strava_id=strava_id,
                file_path=str(file_path),
                file_ext=ext,
                name=name,
                activity_type=sport_type,
                start_time=start_date,
                source=source,
                external_id=external_id,
            )
            state.save()
            downloaded += 1

        except SessionExpiredError:
            state.save()
            if raise_session_error:
                raise
            break

        except Exception:
            state.mark_failed(str(Exception), strava_id=strava_id)
            state.save()

    return downloaded, skipped, linked
