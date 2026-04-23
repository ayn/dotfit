"""Tests for state file management."""

from dotfit.state import ActivityRecord, StateManager


def test_load_empty(tmp_path):
    state = StateManager(tmp_path)
    assert state.records == {}
    assert state.counts() == {}


def test_save_and_load(tmp_path):
    state = StateManager(tmp_path)
    state.mark_downloaded(
        garmin_id=123,
        file_path="/tmp/123.fit",
        name="Morning Run",
        activity_type="running",
        event_type="training",
        start_time="2024-03-15 07:30:00",
    )
    state.save()

    # Load in a new instance
    state2 = StateManager(tmp_path)
    rec = state2.get(123)
    assert rec is not None
    assert rec.activity_name == "Morning Run"
    assert rec.status == "downloaded"
    assert rec.file_path == "/tmp/123.fit"
    assert rec.activity_type == "running"
    assert rec.event_type == "training"


def test_atomic_save(tmp_path):
    """State file should be written atomically (no .tmp leftover)."""
    state = StateManager(tmp_path)
    state.mark_downloaded(garmin_id=1, file_path="/tmp/1.fit")
    state.save()

    assert (tmp_path / ".state.json").exists()
    assert not (tmp_path / ".state.tmp").exists()


def test_mark_uploaded(tmp_path):
    state = StateManager(tmp_path)
    state.mark_downloaded(garmin_id=42, file_path="/tmp/42.fit", name="Ride")
    state.mark_uploaded(42, upload_id=100, strava_id=200)

    rec = state.get(42)
    assert rec.status == "uploaded"
    assert rec.strava_upload_id == 100
    assert rec.strava_activity_id == 200
    assert rec.uploaded_at is not None
    assert rec.error is None


def test_mark_duplicate(tmp_path):
    state = StateManager(tmp_path)
    state.mark_downloaded(garmin_id=7, file_path="/tmp/7.fit")
    state.mark_duplicate(7, strava_id=999)

    rec = state.get(7)
    assert rec.status == "duplicate"
    assert rec.strava_activity_id == 999


def test_mark_failed(tmp_path):
    state = StateManager(tmp_path)
    state.mark_downloaded(garmin_id=8, file_path="/tmp/8.fit", name="Swim")
    state.mark_failed(8, "Connection timeout")

    rec = state.get(8)
    assert rec.status == "failed"
    assert rec.error == "Connection timeout"


def test_pending_uploads(tmp_path):
    state = StateManager(tmp_path)
    state.mark_downloaded(garmin_id=1, file_path="/a.fit")
    state.mark_downloaded(garmin_id=2, file_path="/b.fit")
    state.mark_downloaded(garmin_id=3, file_path="/c.fit")
    state.mark_uploaded(2, upload_id=10, strava_id=20)

    pending = state.pending_uploads()
    ids = {r.garmin_activity_id for r in pending}
    assert ids == {1, 3}


def test_failed_uploads(tmp_path):
    state = StateManager(tmp_path)
    state.mark_downloaded(garmin_id=1, file_path="/a.fit")
    state.mark_downloaded(garmin_id=2, file_path="/b.fit")
    state.mark_failed(2, "timeout")

    failed = state.failed_uploads()
    assert len(failed) == 1
    assert failed[0].garmin_activity_id == 2


def test_counts(tmp_path):
    state = StateManager(tmp_path)
    state.mark_downloaded(garmin_id=1, file_path="/a.fit")
    state.mark_downloaded(garmin_id=2, file_path="/b.fit")
    state.mark_downloaded(garmin_id=3, file_path="/c.fit")
    state.mark_uploaded(1, 10, 20)
    state.mark_duplicate(2, 30)
    state.mark_failed(3, "err")

    counts = state.counts()
    assert counts["uploaded"] == 1
    assert counts["duplicate"] == 1
    assert counts["failed"] == 1


def test_corrupted_state_file(tmp_path):
    """Corrupted state file should not crash — start fresh."""
    (tmp_path / ".state.json").write_text("not valid json {{{")
    state = StateManager(tmp_path)
    assert state.records == {}


def test_upsert_preserves_existing_fields(tmp_path):
    state = StateManager(tmp_path)
    state.mark_downloaded(
        garmin_id=5,
        file_path="/tmp/5.fit",
        name="Long Run",
        activity_type="running",
        start_time="2024-01-01 08:00:00",
    )

    # Upsert with partial update
    state.upsert(
        ActivityRecord(
            garmin_activity_id=5,
            activity_name="Long Run",
            status="uploaded",
            strava_activity_id=999,
        )
    )

    rec = state.get(5)
    assert rec.status == "uploaded"
    assert rec.file_path == "/tmp/5.fit"  # preserved
    assert rec.activity_type == "running"  # preserved
    assert rec.strava_activity_id == 999


def test_downloaded_ids(tmp_path):
    state = StateManager(tmp_path)
    state.mark_downloaded(garmin_id=1, file_path="/a.fit")
    state.mark_downloaded(garmin_id=2, file_path="/b.fit")
    state.mark_uploaded(2, 10, 20)

    ids = state.downloaded_ids()
    assert ids == {1, 2}
