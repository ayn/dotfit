"""Tests for state v2 schema, migration, and lookups."""

import json

from dotfit.state import StateManager

# ── Basic persistence ─────────────────────────────────────────────────


def test_load_empty(tmp_path):
    state = StateManager(tmp_path)
    assert state.records == {}
    assert state.counts()["total"] == 0


def test_save_and_load_v2(tmp_path):
    state = StateManager(tmp_path)
    state.mark_downloaded_from_garmin(
        garmin_id=123,
        file_path="/tmp/123.fit",
        name="Morning Run",
        activity_type="running",
        event_type="training",
        start_time="2024-03-15 07:30:00",
    )
    state.save()

    state2 = StateManager(tmp_path)
    rec = state2.get_by_garmin_id(123)
    assert rec is not None
    assert rec.activity_name == "Morning Run"
    assert rec.downloaded_from == "garmin"
    assert rec.source == "garmin"
    assert rec.file_path == "/tmp/123.fit"
    assert rec.activity_type == "running"
    assert rec.event_type == "training"


def test_v2_file_format(tmp_path):
    state = StateManager(tmp_path)
    state.mark_downloaded_from_garmin(garmin_id=1, file_path="/a.fit")
    state.save()

    data = json.loads((tmp_path / ".state.json").read_text())
    assert data["version"] == 2
    assert "meta" in data
    assert "activities" in data
    assert "g1" in data["activities"]


def test_atomic_save(tmp_path):
    state = StateManager(tmp_path)
    state.mark_downloaded_from_garmin(garmin_id=1, file_path="/tmp/1.fit")
    state.save()

    assert (tmp_path / ".state.json").exists()
    assert not (tmp_path / ".state.tmp").exists()


def test_corrupted_state_file(tmp_path):
    (tmp_path / ".state.json").write_text("not valid json {{{")
    state = StateManager(tmp_path)
    assert state.records == {}


# ── Garmin operations ─────────────────────────────────────────────────


def test_mark_uploaded_to_strava(tmp_path):
    state = StateManager(tmp_path)
    state.mark_downloaded_from_garmin(garmin_id=42, file_path="/tmp/42.fit", name="Ride")
    state.mark_uploaded_to_strava(42, upload_id=100, strava_id=200)

    rec = state.get_by_garmin_id(42)
    assert rec.uploaded_to_strava is True
    assert rec.strava_upload_id == 100
    assert rec.strava_id == 200
    assert rec.error is None


def test_mark_duplicate(tmp_path):
    state = StateManager(tmp_path)
    state.mark_downloaded_from_garmin(garmin_id=7, file_path="/tmp/7.fit")
    state.mark_duplicate(garmin_id=7, strava_id=999)

    rec = state.get_by_garmin_id(7)
    assert rec.uploaded_to_strava is True
    assert rec.strava_id == 999


def test_mark_failed(tmp_path):
    state = StateManager(tmp_path)
    state.mark_downloaded_from_garmin(garmin_id=8, file_path="/tmp/8.fit", name="Swim")
    state.mark_failed("Connection timeout", garmin_id=8)

    rec = state.get_by_garmin_id(8)
    assert rec.error == "Connection timeout"


def test_pending_strava_uploads(tmp_path):
    state = StateManager(tmp_path)
    state.mark_downloaded_from_garmin(garmin_id=1, file_path="/a.fit")
    state.mark_downloaded_from_garmin(garmin_id=2, file_path="/b.fit")
    state.mark_downloaded_from_garmin(garmin_id=3, file_path="/c.fit")
    state.mark_uploaded_to_strava(2, upload_id=10, strava_id=20)

    pending = state.pending_strava_uploads()
    ids = {r.garmin_id for r in pending}
    assert ids == {1, 3}


def test_failed_strava_uploads(tmp_path):
    state = StateManager(tmp_path)
    state.mark_downloaded_from_garmin(garmin_id=1, file_path="/a.fit")
    state.mark_downloaded_from_garmin(garmin_id=2, file_path="/b.fit")
    state.mark_failed("timeout", garmin_id=2)

    failed = state.failed_strava_uploads()
    assert len(failed) == 1
    assert failed[0].garmin_id == 2


def test_counts(tmp_path):
    state = StateManager(tmp_path)
    state.mark_downloaded_from_garmin(garmin_id=1, file_path="/a.fit")
    state.mark_downloaded_from_garmin(garmin_id=2, file_path="/b.fit")
    state.mark_uploaded_to_strava(1, 10, 20)
    state.mark_duplicate(garmin_id=2, strava_id=30)

    counts = state.counts()
    assert counts["downloaded_garmin"] == 2
    assert counts["uploaded_to_strava"] == 2  # both uploaded and duplicate
    assert counts["total"] == 2


# ── Strava pull operations ────────────────────────────────────────────


def test_mark_downloaded_from_strava(tmp_path):
    state = StateManager(tmp_path)
    state.mark_downloaded_from_strava(
        strava_id=999,
        file_path="/tmp/999.fit",
        name="Coros Run",
        activity_type="Run",
        start_time="2024-06-01T08:00:00Z",
        source="coros",
        external_id="coros_abc123",
    )

    rec = state.get_by_strava_id(999)
    assert rec is not None
    assert rec.source == "coros"
    assert rec.downloaded_from == "strava"
    assert rec.strava_external_id == "coros_abc123"


def test_link_strava_to_existing_garmin(tmp_path):
    state = StateManager(tmp_path)
    state.mark_downloaded_from_garmin(
        garmin_id=100, file_path="/a.fit", start_time="2024-01-01 08:00:00"
    )

    state.link_strava_to_existing("g100", strava_id=500, external_id="garmin_push_100")

    rec = state.get_by_garmin_id(100)
    assert rec.strava_id == 500
    assert rec.uploaded_to_strava is True
    assert rec.strava_external_id == "garmin_push_100"

    # Also accessible by strava_id
    assert state.get_by_strava_id(500) is rec


def test_find_by_start_time_exact(tmp_path):
    state = StateManager(tmp_path)
    state.mark_downloaded_from_garmin(
        garmin_id=1, file_path="/a.fit", start_time="2024-03-12T07:15:00Z"
    )

    rec, key = state.find_by_start_time("2024-03-12T07:15:00Z")
    assert rec is not None
    assert key == "g1"


def test_find_by_start_time_within_tolerance(tmp_path):
    state = StateManager(tmp_path)
    state.mark_downloaded_from_garmin(
        garmin_id=1, file_path="/a.fit", start_time="2024-03-12T07:15:00Z"
    )

    # 20 seconds later — within 30s tolerance
    rec, key = state.find_by_start_time("2024-03-12T07:15:20Z")
    assert rec is not None
    assert rec.garmin_id == 1


def test_find_by_start_time_outside_tolerance(tmp_path):
    state = StateManager(tmp_path)
    state.mark_downloaded_from_garmin(
        garmin_id=1, file_path="/a.fit", start_time="2024-03-12T07:15:00Z"
    )

    # 60 seconds later — outside 30s tolerance
    rec, key = state.find_by_start_time("2024-03-12T07:16:00Z")
    assert rec is None


def test_find_by_start_time_different_formats(tmp_path):
    state = StateManager(tmp_path)
    state.mark_downloaded_from_garmin(
        garmin_id=1, file_path="/a.fit", start_time="2024-03-12 07:15:00"
    )

    # ISO format with Z
    rec, _ = state.find_by_start_time("2024-03-12T07:15:05Z")
    assert rec is not None


# ── Meta ──────────────────────────────────────────────────────────────


def test_last_strava_pull(tmp_path):
    state = StateManager(tmp_path)
    assert state.last_strava_pull is None

    state.last_strava_pull = "2024-06-01T12:00:00Z"
    state.save()

    state2 = StateManager(tmp_path)
    assert state2.last_strava_pull == "2024-06-01T12:00:00Z"


# ── V1 migration ─────────────────────────────────────────────────────


def test_v1_migration(tmp_path):
    v1_data = {
        "123": {
            "garmin_activity_id": 123,
            "activity_name": "Morning Run",
            "activity_type": "running",
            "event_type": "training",
            "start_time": "2024-01-01 08:00:00",
            "file_path": "/tmp/123.fit",
            "status": "uploaded",
            "strava_upload_id": 10,
            "strava_activity_id": 20,
            "uploaded_at": "2024-01-02T00:00:00",
            "error": None,
        },
        "456": {
            "garmin_activity_id": 456,
            "activity_name": "Ride",
            "activity_type": "cycling",
            "event_type": "",
            "start_time": "2024-01-02 09:00:00",
            "file_path": "/tmp/456.fit",
            "status": "downloaded",
            "strava_upload_id": None,
            "strava_activity_id": None,
            "uploaded_at": None,
            "error": None,
        },
        "789": {
            "garmin_activity_id": 789,
            "activity_name": "Failed Swim",
            "activity_type": "swimming",
            "event_type": "",
            "start_time": "2024-01-03 10:00:00",
            "file_path": "/tmp/789.fit",
            "status": "failed",
            "strava_upload_id": None,
            "strava_activity_id": None,
            "uploaded_at": None,
            "error": "Connection reset",
        },
    }
    (tmp_path / ".state.json").write_text(json.dumps(v1_data))

    state = StateManager(tmp_path)

    # Backup created
    assert (tmp_path / ".state.json.bak").exists()

    # Uploaded activity
    rec = state.get_by_garmin_id(123)
    assert rec is not None
    assert rec.activity_name == "Morning Run"
    assert rec.uploaded_to_strava is True
    assert rec.strava_id == 20
    assert rec.source == "garmin"
    assert rec.downloaded_from == "garmin"
    assert rec.error is None

    # Downloaded (pending) activity
    rec2 = state.get_by_garmin_id(456)
    assert rec2 is not None
    assert rec2.uploaded_to_strava is False
    assert rec2.file_path == "/tmp/456.fit"

    # Failed activity
    rec3 = state.get_by_garmin_id(789)
    assert rec3 is not None
    assert rec3.error == "Connection reset"

    # Counts
    counts = state.counts()
    assert counts["downloaded_garmin"] == 3
    assert counts["uploaded_to_strava"] == 1
    assert counts["failed"] == 1


def test_v1_migration_preserves_on_save_reload(tmp_path):
    v1_data = {
        "42": {
            "garmin_activity_id": 42,
            "activity_name": "Run",
            "activity_type": "running",
            "event_type": "",
            "start_time": "2024-01-01 08:00:00",
            "file_path": "/tmp/42.fit",
            "status": "uploaded",
            "strava_upload_id": 1,
            "strava_activity_id": 2,
            "uploaded_at": "2024-01-02T00:00:00",
            "error": None,
        }
    }
    (tmp_path / ".state.json").write_text(json.dumps(v1_data))

    # Load (migrates), save (writes v2), reload (reads v2)
    state = StateManager(tmp_path)
    state.save()
    state2 = StateManager(tmp_path)

    rec = state2.get_by_garmin_id(42)
    assert rec is not None
    assert rec.uploaded_to_strava is True

    # Verify it's now v2 format
    data = json.loads((tmp_path / ".state.json").read_text())
    assert data["version"] == 2


# ── Duplicate start times (race events) ──────────────────────────────


def test_duplicate_start_times_separate_records(tmp_path):
    """Two activities at the same time should remain separate records."""
    state = StateManager(tmp_path)
    state.mark_downloaded_from_garmin(
        garmin_id=1, file_path="/a.fit", start_time="2024-06-15 09:00:00"
    )
    state.mark_downloaded_from_garmin(
        garmin_id=2, file_path="/b.fit", start_time="2024-06-15 09:00:00"
    )

    assert state.get_by_garmin_id(1) is not None
    assert state.get_by_garmin_id(2) is not None
    assert state.counts()["total"] == 2
