# dotfit

Local `.fit` archive with bidirectional Garmin/Strava sync. Python CLI.

## Commands

```bash
uv sync --all-extras    # install deps (includes dev + browser-cookie3)
uv run pytest -v        # run tests
uv run ruff check src/ tests/  # lint
uv run dotfit --help    # CLI help
```

## Project layout

```
src/dotfit/
  cli.py               # typer app — all subcommands
  garmin.py             # garminconnect wrapper (auth, download FIT)
  strava.py             # httpx-based strava OAuth client (upload, list activities)
  strava_download.py    # cookie-based download of original files from Strava web
  state.py              # .state.json v2 persistence (atomic writes, v1 migration)
  ratelimit.py          # strava API rate-limit tracker from response headers
  archive.py            # archive/activities/YYYY/MM/ layout (garmin + strava naming)
  activity_types.py     # garmin→strava type mapping, source inference from external_id
  config.py             # pydantic-settings from env/.env
tests/                  # pytest, uses tmp_path fixtures, mocked HTTP, no real API calls
```

## CLI commands

```
dotfit pull-garmin      # download FIT files from Garmin Connect
dotfit pull-strava      # download original files from Strava (for Coros etc.)
dotfit push-strava      # upload FIT files to Strava
dotfit sync             # pull-garmin + pull-strava + push-strava
dotfit upload FILE      # upload a single FIT file (not tracked in state)
dotfit status           # show sync progress
dotfit auth garmin      # authenticate with Garmin Connect
dotfit auth strava      # OAuth with Strava API
dotfit auth strava-cookie  # test/validate session cookie for pull-strava
```

## Key decisions

- **httpx, not stravalib** — our Strava API surface is tiny (OAuth + upload + list + poll). httpx gives direct access to rate-limit headers without fighting stravalib's built-in limiter.
- **State v2 schema** — keyed by source-prefixed IDs (`g{garmin_id}`, `s{strava_id}`), with in-memory indexes. Migrates from v1 automatically with `.bak` backup.
- **Two auth paths for Strava** — OAuth for API calls (upload, list), session cookie for `export_original` web endpoint (download).
- **browser-cookie3 is optional** — `pip install dotfit[browser]`. Falls back to `STRAVA_SESSION_COOKIE` env var.
- **Config at `~/.config/dotfit/`** — tokens stored here, separate from the archive dir.
- **pull-strava is primarily for Coros** — watches that auto-sync to Strava but have no export API.

## Style

- No co-author lines on git commits.
- Keep tests mocked — no real API calls in the test suite.
- Ruff for linting (E, F, I, W rules), line length 100.
