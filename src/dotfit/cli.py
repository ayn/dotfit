"""CLI entry point — all subcommands for dotfit."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import certifi
import typer

# Set SSL cert path before any network libraries load — fixes macOS where
# curl-cffi looks for /etc/ssl/certs/ca-certificates.crt (a Linux path).
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
os.environ.setdefault("CURL_CA_BUNDLE", certifi.where())
from rich.console import Console
from rich.table import Table

from dotfit.activity_types import (
    extract_garmin_id,
    get_workout_type,
    infer_source,
    map_activity_type,
)
from dotfit.archive import Archive
from dotfit.config import get_settings
from dotfit.garmin import GarminClient
from dotfit.ratelimit import RateLimiter
from dotfit.state import StateManager
from dotfit.strava import StravaClient
from dotfit.strava_download import SessionExpiredError, StravaDownloader

app = typer.Typer(
    name="dotfit",
    help="Local .fit archive with bidirectional Garmin/Strava sync.",
    no_args_is_help=True,
)
auth_app = typer.Typer(help="Authenticate with Garmin or Strava.")
app.add_typer(auth_app, name="auth")

console = Console()
logger = logging.getLogger("dotfit")


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


# ── Auth commands ─────────────────────────────────────────────────────


@auth_app.command("garmin")
def auth_garmin(
    email: str = typer.Option(None, envvar="GARMIN_EMAIL", help="Garmin email"),
    password: str = typer.Option(
        None, envvar="GARMIN_PASSWORD", help="Garmin password", hide_input=True
    ),
) -> None:
    """Log in to Garmin Connect and save the session."""
    _setup_logging()
    settings = get_settings()

    if not email:
        email = typer.prompt("Garmin email")
    if not password:
        password = typer.prompt("Garmin password", hide_input=True)

    client = GarminClient(settings.config_dir)
    with console.status("Logging in to Garmin Connect..."):
        try:
            client.login(email, password)
        except Exception as e:
            console.print(f"[red]Login failed:[/red] {e}")
            raise typer.Exit(1)

    console.print("[green]Authenticated with Garmin Connect.[/green]")
    console.print(f"Session saved to {settings.config_dir / 'garmin_tokens'}")


@auth_app.command("strava")
def auth_strava() -> None:
    """Authorize with Strava via OAuth (opens browser)."""
    _setup_logging()
    settings = get_settings()

    if not settings.strava_client_id or not settings.strava_client_secret:
        console.print(
            "[red]Error:[/red] Set STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET in .env first.\n"
            "Create a Strava API app at https://www.strava.com/settings/api\n"
            "Set the callback domain to: localhost"
        )
        raise typer.Exit(1)

    rate_limiter = RateLimiter()
    client = StravaClient(
        client_id=settings.strava_client_id,
        client_secret=settings.strava_client_secret,
        config_dir=settings.config_dir,
        rate_limiter=rate_limiter,
    )
    console.print(
        f"Opening browser for Strava authorization...\n"
        f"If the browser doesn't open, visit the URL printed below.\n"
        f"Callback will be received on localhost:{client.callback_port}"
    )
    try:
        client.authorize()
    except Exception as e:
        console.print(f"[red]Authorization failed:[/red] {e}")
        raise typer.Exit(1)

    console.print("[green]Authenticated with Strava.[/green]")
    console.print(f"Tokens saved to {client.token_path}")


@auth_app.command("strava-cookie")
def auth_strava_cookie() -> None:
    """Test Strava session cookie for pull-strava downloads."""
    _setup_logging()
    settings = get_settings()

    cookie = settings.strava_session_cookie
    if not cookie:
        console.print("No STRAVA_SESSION_COOKIE in env. Trying browser cookies...")
        try:
            downloader = StravaDownloader.create(download_delay=0)
            cookie = downloader.session_cookie
        except RuntimeError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)
    else:
        console.print("Using STRAVA_SESSION_COOKIE from environment.")
        downloader = StravaDownloader(cookie)

    console.print("Testing cookie against strava.com...")
    if downloader.test_cookie():
        console.print("[green]Session cookie is valid.[/green]")
    else:
        console.print(
            "[red]Session cookie is invalid or expired.[/red]\n"
            "Log into strava.com in your browser (Firefox recommended on macOS) and retry."
        )
        raise typer.Exit(1)


# ── Pull from Garmin ──────────────────────────────────────────────────


@app.command("pull-garmin")
def pull_garmin(
    after: str = typer.Option(None, help="Only activities after this date (YYYY-MM-DD)"),
    before: str = typer.Option(None, help="Only activities before this date (YYYY-MM-DD)"),
    force: bool = typer.Option(False, help="Re-download FIT files already on disk"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Download activities from Garmin Connect as FIT files."""
    _setup_logging(verbose)
    settings = get_settings()

    garmin = GarminClient(settings.config_dir)
    if not garmin.resume_session():
        console.print("[red]Not authenticated with Garmin. Run:[/red] dotfit auth garmin")
        raise typer.Exit(1)

    archive = Archive(settings.archive_dir)
    state = StateManager(settings.archive_dir)

    after_date = datetime.strptime(after, "%Y-%m-%d").date() if after else None
    before_date = datetime.strptime(before, "%Y-%m-%d").date() if before else None

    console.print("Fetching activity list from Garmin Connect...")
    activities = garmin.get_all_activities(after=after_date, before=before_date)
    console.print(f"Found [bold]{len(activities)}[/bold] activities on Garmin.")

    to_download = []
    for act in activities:
        act_id = act["activityId"]
        start = act.get("startTimeLocal", "")
        if not force and (
            state.get_by_garmin_id(act_id) or archive.has_fit(act_id, start)
        ):
            continue
        to_download.append(act)

    if not to_download:
        console.print("[green]All activities already downloaded.[/green]")
        return

    console.print(f"Downloading [bold]{len(to_download)}[/bold] new activities...\n")

    downloaded = 0
    skipped = 0
    failed = 0

    for i, act in enumerate(to_download, 1):
        act_id = act["activityId"]
        name = act.get("activityName", f"Activity {act_id}")
        start = act.get("startTimeLocal", "")
        act_type = act.get("activityType", {}).get("typeKey", "unknown")
        event_type = act.get("eventType", {}).get("typeKey", "")

        console.print(f"  [{i}/{len(to_download)}] {name} ({start[:10]}) ", end="")

        try:
            dest = archive.fit_path(act_id, start)
            garmin.download_fit(act_id, dest)

            meta = archive.metadata_path(act_id, start)
            meta.write_text(json.dumps(act, indent=2, default=str))

            state.mark_downloaded_from_garmin(
                garmin_id=act_id,
                file_path=str(dest),
                name=name,
                activity_type=act_type,
                event_type=event_type,
                start_time=start,
            )
            state.save()
            downloaded += 1
            console.print("[green]OK[/green]")

        except Exception as e:
            failed += 1
            if "No data" in str(e) or "manual" in str(e).lower():
                console.print("[yellow]skipped (no FIT)[/yellow]")
                skipped += 1
            else:
                console.print(f"[red]FAILED: {e}[/red]")
                state.mark_downloaded_from_garmin(
                    garmin_id=act_id,
                    file_path="",
                    name=name,
                    activity_type=act_type,
                    event_type=event_type,
                    start_time=start,
                )
                state.mark_failed(str(e), garmin_id=act_id)
                state.save()

    console.print(
        f"\n[bold]Done.[/bold] Downloaded: {downloaded}, "
        f"Skipped: {skipped}, Failed: {failed}"
    )


# ── Pull from Strava ──────────────────────────────────────────────────


@app.command("pull-strava")
def pull_strava(
    after: str = typer.Option(
        None, help="Only activities after this date (YYYY-MM-DD)"
    ),
    before: str = typer.Option(
        None, help="Only activities before this date (YYYY-MM-DD)"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Download original .fit files from Strava into the local archive.

    Useful for archiving activities from watches (e.g. Coros) that auto-sync
    to Strava but don't offer a direct export API. Smart dedup skips activities
    already present from Garmin or previous pulls.
    """
    _setup_logging(verbose)
    settings = get_settings()

    # Need both OAuth client (for listing) and cookie client (for download)
    strava, rate_limiter = _make_strava_client(settings)

    try:
        downloader = StravaDownloader.create(
            session_cookie=settings.strava_session_cookie,
            download_delay=settings.strava_download_delay,
        )
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    archive = Archive(settings.archive_dir)
    state = StateManager(settings.archive_dir)

    # Determine the "after" timestamp for the API call
    after_epoch = None
    if after:
        after_epoch = datetime.strptime(after, "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        ).timestamp()
    elif state.last_strava_pull:
        after_epoch = datetime.fromisoformat(state.last_strava_pull).timestamp()

    before_epoch = None
    if before:
        before_epoch = datetime.strptime(before, "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        ).timestamp()

    console.print("Listing activities from Strava...")
    activities = strava.list_activities(after=after_epoch)

    # Filter by before date if specified
    if before_epoch:
        activities = [
            a for a in activities
            if datetime.fromisoformat(
                a["start_date"].replace("Z", "+00:00")
            ).timestamp() <= before_epoch
        ]

    console.print(f"Found [bold]{len(activities)}[/bold] activities on Strava.\n")

    downloaded = 0
    skipped = 0
    failed = 0
    linked = 0

    for i, act in enumerate(activities, 1):
        strava_id = act["id"]
        name = act.get("name", f"Activity {strava_id}")
        start_date = act.get("start_date", "")
        external_id = act.get("external_id")
        sport_type = act.get("sport_type", "")

        console.print(f"  [{i}/{len(activities)}] {name} ({start_date[:10]}) ", end="")

        # Already known by strava_id?
        if state.get_by_strava_id(strava_id):
            console.print("[dim]already tracked[/dim]")
            skipped += 1
            continue

        # Dedup: try external_id → garmin_id match
        garmin_id = extract_garmin_id(external_id)
        if garmin_id and state.get_by_garmin_id(garmin_id):
            key = state._garmin_idx[garmin_id]
            state.link_strava_to_existing(key, strava_id, external_id)
            state.save()
            console.print(f"[cyan]linked to garmin:{garmin_id}[/cyan]")
            linked += 1
            continue

        # Dedup: fuzzy start_time match (±30s) — only link to pre-existing records
        # (not ones we just downloaded from Strava in this same run)
        existing, existing_key = state.find_by_start_time(start_date, tolerance_seconds=30)
        if existing and existing_key and existing.downloaded_from != "strava":
            state.link_strava_to_existing(existing_key, strava_id, external_id)
            state.save()
            console.print("[cyan]linked by time match[/cyan]")
            linked += 1
            continue

        # New activity — download original file
        source = infer_source(external_id)
        dest_dir = archive.strava_path(start_date, source).parent
        filename_stem = archive._compact_time(start_date) + f"_{source}"

        try:
            file_path, ext = downloader.download_original(strava_id, dest_dir, filename_stem)

            state.mark_downloaded_from_strava(
                strava_id=strava_id,
                file_path=str(file_path),
                file_ext=ext,
                name=name,
                activity_type=sport_type,
                start_time=start_date,
                source=source,
                external_id=external_id,
            )
            state.save()
            downloaded += 1
            console.print(f"[green]OK[/green] ({source}, .{ext})")

        except SessionExpiredError as e:
            console.print(f"\n[red]{e}[/red]")
            state.save()
            raise typer.Exit(1)

        except Exception as e:
            console.print(f"[red]FAILED: {e}[/red]")
            state.mark_failed(str(e), strava_id=strava_id)
            state.save()
            failed += 1

    # Update last_strava_pull timestamp
    state.last_strava_pull = datetime.now(timezone.utc).isoformat()
    state.save()

    console.print(
        f"\n[bold]Done.[/bold] Downloaded: {downloaded}, "
        f"Linked: {linked}, Skipped: {skipped}, Failed: {failed}"
    )


# ── Push to Strava ────────────────────────────────────────────────────


@app.command("push-strava")
def push_strava(
    after: str = typer.Option(None, help="Only upload activities after this date"),
    before: str = typer.Option(None, help="Only upload activities before this date"),
    retry_failed: bool = typer.Option(
        False, "--retry-failed", help="Retry previously failed uploads"
    ),
    force: bool = typer.Option(
        False, "--force", help="Re-upload activities even if already marked as uploaded"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Upload downloaded FIT files to Strava."""
    _setup_logging(verbose)
    settings = get_settings()

    strava, rate_limiter = _make_strava_client(settings)
    state = StateManager(settings.archive_dir)

    if force:
        pending = [
            r for r in state.records.values()
            if r.downloaded_from == "garmin" and r.file_path
        ]
        console.print(f"Force re-uploading [bold]{len(pending)}[/bold] activities...")
    elif retry_failed:
        pending = state.failed_strava_uploads()
        console.print(f"Retrying [bold]{len(pending)}[/bold] previously failed uploads...")
    else:
        pending = state.pending_strava_uploads()

    # Apply date filters
    if after or before:
        after_str = after or ""
        before_str = before or "9999"
        pending = [
            r for r in pending if after_str <= r.start_time[:10] <= before_str
        ]

    if not pending:
        console.print("[green]Nothing to upload.[/green]")
        return

    console.print(
        f"Uploading [bold]{len(pending)}[/bold] activities to Strava...\n"
        f"Rate limits: {rate_limiter.status()}\n"
    )

    uploaded = 0
    duplicates = 0
    failed = 0
    unknown_types: list[str] = []

    for i, rec in enumerate(pending, 1):
        # Pre-check rate limits
        if rate_limiter.daily_exhausted():
            usage = rate_limiter.daily_usage
            console.print(
                f"\n[yellow]Daily rate limit reached ({usage} uploads).[/yellow]\n"
                "Progress saved. Re-run later to continue."
            )
            state.save()
            raise typer.Exit(0)

        if rate_limiter.short_window_exhausted():
            wait = rate_limiter.wait_time()
            console.print(
                f"\n[yellow]15-min rate limit reached. Waiting {wait:.0f}s...[/yellow]"
            )
            time.sleep(wait)

        # Map activity type
        strava_type, is_known = map_activity_type(rec.activity_type)
        if not is_known and rec.activity_type:
            unknown_types.append(f"{rec.activity_name} ({rec.activity_type})")

        console.print(
            f"  [{i}/{len(pending)}] {rec.activity_name} ({rec.start_time[:10]}) ",
            end="",
        )

        if not rec.file_path or not Path(rec.file_path).exists():
            console.print("[red]FIT file missing[/red]")
            state.mark_failed("FIT file not found on disk", garmin_id=rec.garmin_id)
            failed += 1
            continue

        ext_id = str(rec.garmin_id) if rec.garmin_id else None
        result = strava.upload_activity(
            fit_path=Path(rec.file_path),
            name=rec.activity_name or None,
            activity_type=strava_type,
            external_id=ext_id,
        )

        if result.status == "success":
            if rec.garmin_id:
                state.mark_uploaded_to_strava(
                    rec.garmin_id, result.upload_id, result.strava_activity_id
                )
            uploaded += 1
            console.print(
                f"[green]OK[/green] (strava:{result.strava_activity_id})"
            )

            # Set workout type if applicable
            if result.strava_activity_id:
                _maybe_set_workout_type(strava, result.strava_activity_id, rec, strava_type)

        elif result.status == "duplicate":
            state.mark_duplicate(
                garmin_id=rec.garmin_id, strava_id=result.strava_activity_id
            )
            duplicates += 1
            sid = result.strava_activity_id or "?"
            console.print(f"[yellow]duplicate[/yellow] (strava:{sid})")

        elif result.status == "rate_limited":
            console.print("[yellow]rate limited[/yellow]")
            if rate_limiter.daily_exhausted():
                console.print(
                    "\n[yellow]Daily limit reached. Re-run later.[/yellow]"
                )
                state.save()
                raise typer.Exit(0)
            wait = rate_limiter.wait_time()
            console.print(f"  Waiting {wait:.0f}s for rate limit window...")
            time.sleep(wait)
            state.mark_failed("Rate limited, retry later", garmin_id=rec.garmin_id)
            failed += 1

        else:
            state.mark_failed(
                result.error or "Unknown error", garmin_id=rec.garmin_id
            )
            failed += 1
            console.print(f"[red]FAILED: {result.error}[/red]")

        state.save()

    console.print(
        f"\n[bold]Done.[/bold] Uploaded: {uploaded}, "
        f"Duplicates: {duplicates}, Failed: {failed}"
    )

    if unknown_types:
        console.print(
            "\n[yellow]Unknown activity types (uploaded with default type):[/yellow]"
        )
        for entry in unknown_types:
            console.print(f"  - {entry}")
        console.print(
            "Edit these on Strava if the type is wrong. "
            "Activity IDs are in .state.json."
        )


def _maybe_set_workout_type(
    strava: StravaClient, strava_id: int, rec, strava_type: str | None
) -> None:
    """Set Strava workout_type based on event type, activity type, name, or FIT contents."""
    from dotfit.fit_utils import has_intervals

    wt = get_workout_type(
        rec.event_type,
        strava_type,
        activity_type=rec.activity_type,
        activity_name=rec.activity_name,
    )

    # If no workout tag yet, check FIT file for structured intervals
    if wt is None and rec.file_path and Path(rec.file_path).exists():
        if has_intervals(Path(rec.file_path)):
            sport_types = {"run": 3, "ride": 12}
            wt = sport_types.get(strava_type)

    if wt is not None:
        try:
            strava.update_activity(strava_id, workout_type=wt)
        except Exception as e:
            logger.warning("Failed to set workout_type on %s: %s", strava_id, e)


# ── Sync ──────────────────────────────────────────────────────────────


@app.command()
def sync(
    after: str = typer.Option(None, help="Only activities after this date"),
    before: str = typer.Option(None, help="Only activities before this date"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Pull from Garmin and push to Strava (pull-garmin + push-strava)."""
    pull_garmin(after=after, before=before, force=False, verbose=verbose)
    push_strava(after=after, before=before, retry_failed=False, verbose=verbose)


# ── Upload (single file) ─────────────────────────────────────────────


@app.command()
def upload(
    fit_file: Path = typer.Argument(..., help="Path to a .fit file"),
    activity_type: str = typer.Option(
        None, "--type", "-t", help="Strava activity type (e.g. run, ride, swim)"
    ),
    name: str = typer.Option(None, "--name", "-n", help="Activity name on Strava"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Upload a single FIT file to Strava (not tracked in state)."""
    _setup_logging(verbose)
    settings = get_settings()

    if not fit_file.exists():
        console.print(f"[red]File not found:[/red] {fit_file}")
        raise typer.Exit(1)

    strava, _ = _make_strava_client(settings)

    console.print(f"Uploading {fit_file.name}...")
    result = strava.upload_activity(
        fit_path=fit_file,
        name=name,
        activity_type=activity_type,
    )

    if result.status == "success":
        url = f"https://www.strava.com/activities/{result.strava_activity_id}"
        console.print(f"[green]Uploaded![/green] {url}")
    elif result.status == "duplicate":
        sid = result.strava_activity_id
        url = f"https://www.strava.com/activities/{sid}" if sid else "?"
        console.print(f"[yellow]Duplicate of existing activity:[/yellow] {url}")
    else:
        console.print(f"[red]Upload failed:[/red] {result.error}")
        raise typer.Exit(1)


# ── Status ────────────────────────────────────────────────────────────


@app.command()
def status(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Show sync progress and state summary."""
    _setup_logging(verbose)
    settings = get_settings()
    state = StateManager(settings.archive_dir)

    counts = state.counts()

    if counts["total"] == 0:
        console.print("No activities tracked yet. Run [bold]dotfit pull-garmin[/bold] first.")
        return

    table = Table(title="Sync Status")
    table.add_column("Metric", style="bold")
    table.add_column("Count", justify="right")

    rows = [
        ("Downloaded from Garmin", counts["downloaded_garmin"], "cyan"),
        ("Downloaded from Strava", counts["downloaded_strava"], "cyan"),
        ("Uploaded to Strava", counts["uploaded_to_strava"], "green"),
        ("Pending upload", counts["pending_upload"], "yellow"),
        ("Failed", counts["failed"], "red"),
        ("Total activities", counts["total"], "bold"),
    ]
    for label, count, style in rows:
        table.add_row(f"[{style}]{label}[/{style}]", str(count))

    console.print(table)

    if state.last_strava_pull:
        console.print(f"\nLast Strava pull: {state.last_strava_pull}")

    # Show failed activities
    failed = state.failed_strava_uploads()
    if failed:
        console.print(f"\n[red]Failed uploads ({len(failed)}):[/red]")
        for rec in failed[:20]:
            console.print(f"  {rec.activity_name} — {rec.error}")
        if len(failed) > 20:
            console.print(f"  ... and {len(failed) - 20} more")
        console.print("\nRetry with: [bold]dotfit push-strava --retry-failed[/bold]")


# ── Helpers ───────────────────────────────────────────────────────────


def _make_strava_client(settings) -> tuple[StravaClient, RateLimiter]:
    if not settings.strava_client_id or not settings.strava_client_secret:
        console.print(
            "[red]Error:[/red] Set STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET in .env"
        )
        raise typer.Exit(1)

    rate_limiter = RateLimiter(settings.strava_rate_limit_15min, settings.strava_rate_limit_daily)
    client = StravaClient(
        client_id=settings.strava_client_id,
        client_secret=settings.strava_client_secret,
        config_dir=settings.config_dir,
        rate_limiter=rate_limiter,
    )

    try:
        client.load_tokens()
    except RuntimeError:
        console.print("[red]Not authenticated with Strava. Run:[/red] dotfit auth strava")
        raise typer.Exit(1)

    return client, rate_limiter
