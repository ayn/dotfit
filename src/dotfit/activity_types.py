"""Garmin → Strava activity type mapping."""

# Garmin activityType.typeKey → Strava activity_type for uploads.
# None means: don't override, let Strava infer from the FIT file.
GARMIN_TO_STRAVA: dict[str, str | None] = {
    # Running
    "running": "run",
    "trail_running": "run",
    "treadmill_running": "run",
    "track_running": "run",
    "virtual_run": "virtualrun",
    # Cycling
    "cycling": "ride",
    "mountain_biking": "ride",
    "indoor_cycling": "ride",
    "gravel_cycling": "ride",
    "road_biking": "ride",
    "virtual_ride": "virtualride",
    "e_bike_ride": "ebikeride",
    # Swimming
    "swimming": "swim",
    "lap_swimming": "swim",
    "open_water_swimming": "swim",
    "pool_swimming": "swim",
    # Outdoor / adventure
    "hiking": "hike",
    "walking": "walk",
    "rock_climbing": "rockclimbing",
    "skiing": "alpineski",
    "resort_skiing_snowboarding_ws": "alpineski",
    "backcountry_skiing": "backcountryski",
    "cross_country_skiing": "nordicski",
    "cross_country_skiing_ws": "nordicski",
    "snowboarding": "snowboard",
    "snowboarding_ws": "snowboard",
    "snowshoeing": "snowshoe",
    "snowshoeing_ws": "snowshoe",
    "ice_skating": "iceskate",
    "inline_skating": "inlineskate",
    "surfing": "surfing",
    "windsurfing": "windsurf",
    "kiteboarding": "kitesurf",
    "sailing": "sail",
    "kayaking": "kayaking",
    "whitewater_kayaking_rafting": "kayaking",
    "stand_up_paddleboarding": "standuppaddling",
    # Fitness / gym
    "strength_training": "weighttraining",
    "yoga": "yoga",
    "pilates": "workout",
    "elliptical": "elliptical",
    "rowing": "rowing",
    "indoor_rowing": "rowing",
    "stair_climbing": "stairstepper",
    "cardio": "workout",
    "hiit": "workout",
    "fitness_equipment": "workout",
    # Mind / body
    "meditation": None,
    "breathwork": None,
    # Other
    "golf": "golf",
    "soccer": "soccer",
    "tennis": "workout",
    "pickleball": "workout",
    "racquet_ball": "workout",
    "multi_sport": None,
    "transition": None,
    "other": None,
}

# Strava workout_type enum values by sport
STRAVA_WORKOUT_TYPES: dict[str, dict[str, int]] = {
    "run": {"default": 0, "race": 1, "long_run": 2, "workout": 3},
    "ride": {"default": 10, "race": 11, "workout": 12},
}


def map_activity_type(garmin_type_key: str) -> tuple[str | None, bool]:
    """Map a Garmin activity type to a Strava activity_type string.

    Returns:
        (strava_type, is_known) — if is_known is False, the Garmin type
        wasn't in our mapping table and will need manual review.
    """
    normalized = garmin_type_key.lower().strip()
    if normalized in GARMIN_TO_STRAVA:
        return GARMIN_TO_STRAVA[normalized], True
    return None, False


def get_workout_type(garmin_event_type: str, strava_sport: str | None) -> int | None:
    """Return a Strava workout_type int if the Garmin event indicates a workout or race."""
    if not strava_sport or not garmin_event_type:
        return None

    sport_types = STRAVA_WORKOUT_TYPES.get(strava_sport)
    if not sport_types:
        return None

    event = garmin_event_type.lower().strip()
    if "training" in event or "workout" in event:
        return sport_types.get("workout")
    if "race" in event:
        return sport_types.get("race")
    return None
