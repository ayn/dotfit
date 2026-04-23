# dotfit

Local `.fit` archive with bidirectional Garmin/Strava sync. Python CLI.

## Commands

```bash
uv sync --all-extras    # install deps (includes dev)
uv run pytest -v        # run tests
uv run ruff check src/ tests/  # lint
uv run dotfit --help    # CLI help
```

## Project layout

```
src/dotfit/           # package source (hatchling src layout)
  cli.py              # typer app — all subcommands
  garmin.py           # garminconnect wrapper (auth, download FIT)
  strava.py           # httpx-based strava client (OAuth, upload)
  state.py            # .state.json persistence (atomic writes)
  ratelimit.py        # strava rate-limit tracker from response headers
  archive.py          # archive/activities/YYYY/MM/<id>.fit layout
  activity_types.py   # garmin→strava type mapping
  config.py           # pydantic-settings from env/.env
tests/                # pytest, uses tmp_path fixtures, no real API calls
```

## Key decisions

- **httpx, not stravalib** — our Strava API surface is tiny (OAuth + upload + poll). httpx gives direct access to rate-limit headers without fighting stravalib's built-in limiter. Saves the pint dependency.
- **JSON state file, no DB** — `.state.json` in the archive dir. Atomic writes (tmp + rename). Sufficient for single-user CLI.
- **Config at `~/.config/dotfit/`** — tokens stored here (garmin session, strava OAuth), separate from the archive dir so the archive can be synced/shared.
- **CLI command is `dotfit`** — single entry point, subcommands: `pull`, `push`, `sync`, `upload`, `status`, `auth garmin`, `auth strava`.

## Style

- No co-author lines on git commits.
- Keep tests mocked — no real API calls in the test suite.
- Ruff for linting (E, F, I, W rules), line length 100.
