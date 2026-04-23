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
    "stopwatch": None,
    "stop_watch": None,
}

# Activity types that should be skipped during upload (no useful data for Strava)
SKIP_UPLOAD_TYPES: set[str] = {
    "stopwatch",
    "stop_watch",
    "breathwork",
    "meditation",
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


def infer_source(external_id: str | None) -> str:
    """Infer activity source from Strava's external_id field."""
    if not external_id:
        return "strava"
    eid = external_id.lower().strip()
    if eid.startswith("garmin_push_") or eid.startswith("garmin_"):
        return "garmin"
    if eid.isdigit():
        return "garmin"  # likely uploaded via dotfit or Garmin Connect sync
    if "coros" in eid:
        return "coros"
    if "wahoo" in eid:
        return "wahoo"
    if "zwift" in eid:
        return "zwift"
    if "suunto" in eid:
        return "suunto"
    if "polar" in eid:
        return "polar"
    return "unknown"


def extract_garmin_id(external_id: str | None) -> int | None:
    """Try to extract a Garmin activity ID from Strava's external_id."""
    if not external_id:
        return None
    eid = external_id.strip()
    # garmin_push_123456789
    if eid.lower().startswith("garmin_push_"):
        try:
            return int(eid[12:])
        except ValueError:
            return None
    # Pure numeric — likely a garmin ID from our own uploads
    if eid.isdigit():
        try:
            return int(eid)
        except ValueError:
            return None
    return None


# Activity types that are inherently workouts (always tag as workout on Strava)
ALWAYS_WORKOUT_TYPES: set[str] = {
    "track_running",
}

# Substrings in activity name that indicate a workout
WORKOUT_NAME_PATTERNS: list[str] = [
    "track running",
]


def get_workout_type(
    garmin_event_type: str,
    strava_sport: str | None,
    *,
    activity_type: str | None = None,
    activity_name: str | None = None,
) -> int | None:
    """Return a Strava workout_type int if the activity should be tagged.

    Checks (in order):
    1. Explicit race/training event type from Garmin
    2. Activity type that is inherently a workout (e.g. track_running)
    3. Activity name containing workout indicators (e.g. "Track Running")
    """
    if not strava_sport:
        return None

    sport_types = STRAVA_WORKOUT_TYPES.get(strava_sport)
    if not sport_types:
        return None

    # Check explicit event type first — race takes priority
    if garmin_event_type:
        event = garmin_event_type.lower().strip()
        if "race" in event:
            return sport_types.get("race")
        if "training" in event or "workout" in event:
            return sport_types.get("workout")

    # Activity types that are always workouts
    if activity_type and activity_type.lower().strip() in ALWAYS_WORKOUT_TYPES:
        return sport_types.get("workout")

    # Name-based detection (e.g. "Shilin District Track Running")
    if activity_name:
        name_lower = activity_name.lower()
        for pattern in WORKOUT_NAME_PATTERNS:
            if pattern in name_lower:
                return sport_types.get("workout")

    return None
