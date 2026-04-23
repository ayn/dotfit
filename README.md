# dotfit

Local `.fit` archive with bidirectional Garmin/Strava sync. Built for migrating off a Garmin watch (e.g. to Coros) while preserving training history — and continuing to archive new activities afterward.

## What it does

- **Pull from Garmin**: downloads all activities as original `.fit` files
- **Pull from Strava**: downloads original files for activities synced from other devices (e.g. Coros watches that auto-sync to Strava but have no export API)
- **Push to Strava**: uploads `.fit` files to your Strava account
- **Smart dedup**: cross-references activities by Garmin ID and start time so nothing gets downloaded or uploaded twice
- **Incremental + resumable**: re-running fetches only new activities, skips what's already done
- **Rate-limit aware**: respects Strava's API limits, backs off automatically, persists progress

Organizes a local archive: `archive/activities/YYYY/MM/<id>.fit`

## Install

Requires Python 3.11+.

```bash
git clone https://github.com/YOUR_USER/dotfit.git
cd dotfit
uv sync

# Optional: install browser-cookie3 for automatic Strava cookie reading
uv sync --extra browser
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

Sessions and tokens are saved to `~/.config/dotfit/` for reuse.

## Usage

```bash
# Download all activities from Garmin
dotfit pull-garmin

# Download original files from Strava (e.g. Coros activities)
dotfit pull-strava

# Upload all downloaded activities to Strava
dotfit push-strava

# Or do all three in one command
dotfit sync

# Filter by date
dotfit sync --after 2024-01-01 --before 2024-12-31

# Upload a single FIT file directly
dotfit upload path/to/activity.fit --type run --name "Morning Run"

# Check progress
dotfit status

# Retry failed uploads
dotfit push-strava --retry-failed
```

## Pull from Strava

The `pull-strava` command is primarily for archiving activities from watches like **Coros** that auto-sync to Strava but don't offer a user-accessible export API. It downloads the original `.fit` (or `.gpx`/`.tcx`) file from Strava for any activity not already in your local archive.

### How it works

1. Lists your activities via the Strava OAuth API
2. For each activity, checks if it's already in the archive (by Garmin ID match or start-time match within ±30 seconds)
3. New activities are downloaded via Strava's `export_original` web endpoint

### Session cookie setup

The `export_original` endpoint requires a browser session cookie (not OAuth). Two options:

**Option A: Automatic (recommended)**

Install `browser-cookie3` and log into strava.com in Firefox:

```bash
uv sync --extra browser
# Log into strava.com in Firefox, then:
dotfit auth strava-cookie   # verifies the cookie works
dotfit pull-strava
```

Firefox is recommended on macOS. Chrome's App Bound Encryption (since Chrome 127) can prevent third-party cookie reading.

**Option B: Manual paste**

1. Log into strava.com in any browser
2. Open DevTools → Application → Cookies → `_strava4_session`
3. Copy the value and add to `.env`:

```
STRAVA_SESSION_COOKIE=<paste here>
```

### Important notes

- `export_original` is an **unofficial web endpoint** — Strava could change it without notice.
- For archiving your own account's data, this is commonly accepted practice. Forkers should be aware of Strava's ToS.
- Downloads are throttled (1.5s between requests by default, configurable via `STRAVA_DOWNLOAD_DELAY`).

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
│           ├── 123456.fit                  # From Garmin (by activity ID)
│           ├── 123456.json                 # Garmin metadata
│           └── 20240315-073000_coros.fit   # From Strava (by timestamp + source)
└── .state.json                             # Sync state (v2 schema)
```

State is a JSON file with version 2 schema, keyed by source-prefixed IDs (`g123456` for Garmin, `s987654` for Strava-only). Tracks which activities are downloaded, uploaded, or linked across sources.

## Garmin MFA

Garmin Connect uses SSO with optional MFA. The `garth` library (used under the hood) handles this — you may be prompted for an MFA code during `dotfit auth garmin`. The session is saved and refreshed automatically on subsequent runs.

If your session expires, just re-run `dotfit auth garmin`.

## Disclaimer

This tool uses third-party APIs (Garmin Connect, Strava) that may change without notice. It is not affiliated with or endorsed by Garmin or Strava. Use at your own risk.

- Garmin's API is unofficial and undocumented — authentication flows may break when Garmin updates their SSO.
- Strava's `export_original` is an unofficial web endpoint that could break at any time.
- The Strava OAuth API is public but subject to rate limits and terms of service.

## Future enhancements

The `pull-strava` command currently requires a manual session cookie (pasted from browser devtools, or auto-read via `browser-cookie3`). Strava cookies are fixed-lifetime (~30 days) and don't auto-extend on use. Manual refresh every few weeks is acceptable for personal-scale archival but not ideal.

If Strava ever exposes original-file download through the official OAuth API, most of this becomes moot. Worth re-evaluating periodically.

Below are deferred ideas for automating cookie refresh and sync triggering. None are implemented — they're documented here so future contributors can pick them up without re-deriving the tradeoffs.

### launchd WatchPaths + dedicated browser profile (macOS)

Create a dedicated Chrome/Firefox profile for the Strava account being archived. A `launchd` agent with `WatchPaths` on that profile's cookie SQLite DB triggers sync whenever cookies change — script reads fresh `_strava4_session` via `browser-cookie3` (scoped to the specific profile path) and runs `dotfit sync`.

- **Pros:** zero-friction once set up, no cross-contamination with your main Strava account
- **Cons:** requires a persistent profile (incompatible with private browsing), Chrome's cookie encryption requires Keychain prompt on first run
- **Gotcha:** verify cookie's athlete via `/api/v3/athlete` before syncing to prevent accidental mixup if the profile gets re-logged to a different account

### Browser extension + native messaging (cross-platform)

A tiny extension scoped to `strava.com` detects successful login, then calls a native host binary that triggers `dotfit sync`.

- **Pros:** clean UX, proper security scope, zero spurious runs
- **Cons:** extension maintenance burden, native host install complexity — overkill for a personal tool

### Menu bar trigger (xbar / SwiftBar, macOS)

A menu bar icon that runs `dotfit sync` on click and displays last-sync status.

- **Pros:** trivially simple, near-zero maintenance
- **Cons:** still manual (one click per sync), macOS only

### Fully automated reauth: Playwright + IMAP

A dedicated email account (or label + app password) receives Strava's login codes. When the main script detects cookie expiry, a Playwright flow performs login: enters email, waits for code via IMAP, enters code, captures new session cookie.

- **Pros:** fully hands-off indefinitely
- **Cons:** brittle (Strava login page changes break it), stores email app-password, squarely in "clever script fighting the platform" territory — not recommended unless manual reauth becomes seriously annoying

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
