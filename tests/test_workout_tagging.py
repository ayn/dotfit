"""Tests for workout tagging logic — track running and interval detection."""

from dotfit.activity_types import get_workout_type


class TestTrackRunningAlwaysWorkout:
    def test_track_running_by_activity_type(self):
        """track_running activityType should be tagged as workout."""
        result = get_workout_type("", "run", activity_type="track_running")
        assert result == 3  # Strava workout_type for run workout

    def test_track_running_by_name(self):
        """Activity name containing 'Track Running' should be tagged as workout."""
        result = get_workout_type(
            "", "run", activity_name="Shilin District Track Running"
        )
        assert result == 3

    def test_track_running_name_case_insensitive(self):
        result = get_workout_type("", "run", activity_name="morning track running")
        assert result == 3

    def test_track_running_race_event_stays_race(self):
        """If explicitly marked as race, respect that over the track default."""
        result = get_workout_type(
            "race", "run", activity_name="Shilin District Track Running"
        )
        assert result == 1  # race takes priority

    def test_track_running_training_event(self):
        """Training event + track = workout (same result either way)."""
        result = get_workout_type("training", "run", activity_type="track_running")
        assert result == 3

    def test_non_track_still_needs_event(self):
        """Regular running without event type should NOT be tagged."""
        result = get_workout_type("", "run", activity_type="running")
        assert result is None

    def test_non_track_name_not_tagged(self):
        """Normal run name should NOT trigger workout tagging."""
        result = get_workout_type("", "run", activity_name="Morning Run")
        assert result is None

    def test_backward_compat_no_activity_type(self):
        """Existing calls without activity_type still work."""
        assert get_workout_type("training", "run") == 3
        assert get_workout_type("race", "run") == 1
        assert get_workout_type("", "run") is None
