"""Tests for activity type mapping."""

from dotfit.activity_types import get_workout_type, map_activity_type


class TestMapActivityType:
    def test_running(self):
        assert map_activity_type("running") == ("run", True)

    def test_trail_running(self):
        assert map_activity_type("trail_running") == ("run", True)

    def test_cycling(self):
        assert map_activity_type("cycling") == ("ride", True)

    def test_lap_swimming(self):
        assert map_activity_type("lap_swimming") == ("swim", True)

    def test_open_water_swimming(self):
        assert map_activity_type("open_water_swimming") == ("swim", True)

    def test_strength_training(self):
        assert map_activity_type("strength_training") == ("weighttraining", True)

    def test_yoga(self):
        assert map_activity_type("yoga") == ("yoga", True)

    def test_meditation_maps_to_none(self):
        """Meditation has no Strava equivalent — upload without type override."""
        strava_type, is_known = map_activity_type("meditation")
        assert strava_type is None
        assert is_known is True  # known but unmappable

    def test_unknown_type(self):
        strava_type, is_known = map_activity_type("underwater_basket_weaving")
        assert strava_type is None
        assert is_known is False

    def test_case_insensitive(self):
        assert map_activity_type("RUNNING") == ("run", True)
        assert map_activity_type("  Cycling  ") == ("ride", True)

    def test_hiking(self):
        assert map_activity_type("hiking") == ("hike", True)

    def test_walking(self):
        assert map_activity_type("walking") == ("walk", True)

    def test_indoor_cycling(self):
        assert map_activity_type("indoor_cycling") == ("ride", True)


class TestGetWorkoutType:
    def test_running_workout(self):
        assert get_workout_type("training", "run") == 3

    def test_running_race(self):
        assert get_workout_type("race", "run") == 1

    def test_cycling_workout(self):
        assert get_workout_type("training", "ride") == 12

    def test_cycling_race(self):
        assert get_workout_type("race", "ride") == 11

    def test_no_event_type(self):
        assert get_workout_type("", "run") is None
        assert get_workout_type(None, "run") is None

    def test_unsupported_sport(self):
        assert get_workout_type("training", "swim") is None
        assert get_workout_type("training", "hike") is None

    def test_default_event(self):
        assert get_workout_type("uncategorized", "run") is None

    def test_no_sport(self):
        assert get_workout_type("training", None) is None
