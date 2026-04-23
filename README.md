# dotfit

Mirror your Garmin Connect activity history to Strava. Built for migrating off a Garmin watch while preserving your training history.

## What it does

- Downloads all activities from Garmin Connect as original `.fit` files
- Uploads every `.fit` to your Strava account
- **Incremental + resumable**: re-running fetches only new activities and skips already-uploaded files
- **Rate-limit aware**: respects Strava's API limits, backs off automatically, persists progress so you can re-run later
- Organizes a local archive: `archive/activities/YYYY/MM/<id>.fit`

## Install

Requires Python 3.11+.

```bash
# Clone and install
git clone https://github.com/YOUR_USER/dotfit.git
cd dotfit
uv sync

# Or install directly
uv pip install .
```

## Setup

### 1. Create a Strava API application

1. Go to [https://www.strava.com/settings/api](https://www.strava.com/settings/api)
2. Create a new application:
   - **Application Name**: dotfit (or anything)
   - **Category**: Other
   - **Website**: http://localhost
   - **Authorization Callback Domain**: `localhost`
3. Note your **Client ID** and **Client Secret**

### 2. Configure credentials

```bash
cp .env.example .env
```

Edit `.env`:

```
STRAVA_CLIENT_ID=12345
STRAVA_CLIENT_SECRET=abcdef1234567890
GARMIN_EMAIL=you@example.com
```

### 3. Authenticate

```bash
# Log in to Garmin Connect (will prompt for password, handles MFA)
dotfit auth garmin

# Authorize with Strava (opens browser for OAuth)
dotfit auth strava
```

Garmin sessions and Strava tokens are saved to `~/.config/dotfit/` for reuse.

## Usage

```bash
# Download all activities from Garmin
dotfit pull

# Upload all downloaded activities to Strava
dotfit push

# Or do both in one command
dotfit sync

# Filter by date
dotfit sync --after 2024-01-01 --before 2024-12-31

# Upload a single FIT file directly
dotfit upload path/to/activity.fit --type run --name "Morning Run"

# Check progress
dotfit status

# Retry failed uploads
dotfit push --retry-failed
```

## Rate limiting

Strava enforces two rate-limit windows:

| Window    | Default limit | Our default |
|-----------|--------------|-------------|
| 15 minute | 600 requests | 90 uploads  |
| Daily     | 30,000 req   | 900 uploads |

The tool tracks usage via Strava's `X-RateLimit-*` response headers and pauses automatically:

- **15-min limit hit**: waits until the next quarter-hour, then resumes
- **Daily limit hit**: saves progress, exits cleanly with a message about when to re-run

Override the conservative defaults via environment variables:

```
STRAVA_RATE_LIMIT_15MIN=150
STRAVA_RATE_LIMIT_DAILY=1500
```

## Activity type mapping

Common Garmin types (run, ride, swim, hike, walk, strength, yoga, etc.) are mapped to Strava equivalents automatically. Activities marked as workouts or races on Garmin get the corresponding `workout_type` set on Strava.

Unknown activity types are uploaded with Strava's default (inferred from the FIT file). The tool warns you about these so you can edit them on Strava — activity IDs are printed and stored in `.state.json`.

## Architecture

```
archive/
├── activities/
│   └── 2024/
│       └── 03/
│           ├── 123456.fit      # Original FIT file
│           └── 123456.json     # Garmin metadata
└── .state.json                 # Sync state (what's downloaded/uploaded)
```

State is a flat JSON file mapping Garmin activity IDs to their sync status (`downloaded`, `uploaded`, `duplicate`, `failed`). No database required.

## Garmin MFA

Garmin Connect uses SSO with optional MFA. The `garth` library (used under the hood) handles this — you may be prompted for an MFA code during `dotfit auth garmin`. The session is saved and refreshed automatically on subsequent runs.

If your session expires, just re-run `dotfit auth garmin`.

## Disclaimer

This tool uses third-party APIs (Garmin Connect, Strava) that may change without notice. It is not affiliated with or endorsed by Garmin or Strava. Use at your own risk.

Garmin's API is unofficial and undocumented — authentication flows may break when Garmin updates their SSO. The Strava API is public but subject to rate limits and terms of service.

## Development

```bash
uv sync --all-extras
uv run pytest -v
uv run ruff check src/ tests/
```

## Contributing

Issues and PRs welcome. Please include tests for new functionality.

## License

MIT
