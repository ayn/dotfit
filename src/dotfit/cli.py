"""CLI entry point — all subcommands for dotfit."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from dotfit.activity_types import get_workout_type, map_activity_type
from dotfit.archive import Archive
from dotfit.config import get_settings
from dotfit.garmin import GarminClient
from dotfit.ratelimit import RateLimiter
from dotfit.state import StateManager
from dotfit.strava import StravaClient

app = typer.Typer(
    name="dotfit",
    help="Mirror Garmin Connect activity history to Strava.",
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


# ── Pull ──────────────────────────────────────────────────────────────


@app.command()
def pull(
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
        if not force and (state.get(act_id) or archive.has_fit(act_id, start)):
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

            # Save Garmin metadata alongside the FIT
            meta = archive.metadata_path(act_id, start)
            meta.write_text(json.dumps(act, indent=2, default=str))

            state.mark_downloaded(
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
                state.mark_downloaded(
                    garmin_id=act_id,
                    file_path="",
                    name=name,
                    activity_type=act_type,
                    event_type=event_type,
                    start_time=start,
                )
                state.mark_failed(act_id, str(e))
                state.save()

    console.print(
        f"\n[bold]Done.[/bold] Downloaded: {downloaded}, "
        f"Skipped: {skipped}, Failed: {failed}"
    )


# ── Push ──────────────────────────────────────────────────────────────


@app.command()
def push(
    after: str = typer.Option(None, help="Only upload activities after this date"),
    before: str = typer.Option(None, help="Only upload activities before this date"),
    retry_failed: bool = typer.Option(
        False, "--retry-failed", help="Retry previously failed uploads"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Upload downloaded FIT files to Strava."""
    _setup_logging(verbose)
    settings = get_settings()

    strava, rate_limiter = _make_strava_client(settings)
    state = StateManager(settings.archive_dir)

    if retry_failed:
        pending = state.failed_uploads()
        console.print(f"Retrying [bold]{len(pending)}[/bold] previously failed uploads...")
    else:
        pending = state.pending_uploads()

    # Apply date filters
    if after or before:
        after_str = after or ""
        before_str = before or "9999"
        pending = [
            r
            for r in pending
            if after_str <= r.start_time[:10] <= before_str
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
            state.mark_failed(rec.garmin_activity_id, "FIT file not found on disk")
            failed += 1
            continue

        result = strava.upload_activity(
            fit_path=Path(rec.file_path),
            name=rec.activity_name or None,
            activity_type=strava_type,
            external_id=str(rec.garmin_activity_id),
        )

        if result.status == "success":
            state.mark_uploaded(
                rec.garmin_activity_id, result.upload_id, result.strava_activity_id
            )
            uploaded += 1
            console.print(
                f"[green]OK[/green] (strava:{result.strava_activity_id})"
            )

            # Set workout type if applicable
            if result.strava_activity_id:
                _maybe_set_workout_type(strava, result.strava_activity_id, rec, strava_type)

        elif result.status == "duplicate":
            state.mark_duplicate(rec.garmin_activity_id, result.strava_activity_id)
            duplicates += 1
            sid = result.strava_activity_id or "?"
            console.print(f"[yellow]duplicate[/yellow] (strava:{sid})")

        elif result.status == "rate_limited":
            console.print("[yellow]rate limited[/yellow]")
            wait = rate_limiter.wait_time()
            if rate_limiter.daily_exhausted():
                console.print(
                    "\n[yellow]Daily limit reached. Re-run later.[/yellow]"
                )
                state.save()
                raise typer.Exit(0)
            console.print(f"  Waiting {wait:.0f}s for rate limit window...")
            time.sleep(wait)
            # Will retry on next loop iteration... but we already moved past this one.
            # Mark as failed so --retry-failed picks it up.
            state.mark_failed(rec.garmin_activity_id, "Rate limited, retry later")
            failed += 1

        else:
            state.mark_failed(rec.garmin_activity_id, result.error or "Unknown error")
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
    """Set Strava workout_type if Garmin event indicates a workout or race."""
    wt = get_workout_type(rec.event_type, strava_type)
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
    """Pull from Garmin then push to Strava (pull + push)."""
    pull(after=after, before=before, force=False, verbose=verbose)
    push(after=after, before=before, retry_failed=False, verbose=verbose)


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
    total = sum(counts.values())

    if total == 0:
        console.print("No activities tracked yet. Run [bold]dotfit pull[/bold] first.")
        return

    table = Table(title="Sync Status")
    table.add_column("Status", style="bold")
    table.add_column("Count", justify="right")

    status_styles = {
        "downloaded": "cyan",
        "uploaded": "green",
        "duplicate": "yellow",
        "failed": "red",
        "pending": "dim",
    }
    for s in ("downloaded", "uploaded", "duplicate", "failed", "pending"):
        count = counts.get(s, 0)
        style = status_styles.get(s, "")
        table.add_row(f"[{style}]{s}[/{style}]", str(count))

    table.add_row("[bold]Total[/bold]", f"[bold]{total}[/bold]")
    console.print(table)

    # Show failed activities
    failed = state.failed_uploads()
    if failed:
        console.print(f"\n[red]Failed uploads ({len(failed)}):[/red]")
        for rec in failed[:20]:
            console.print(f"  {rec.activity_name} — {rec.error}")
        if len(failed) > 20:
            console.print(f"  ... and {len(failed) - 20} more")
        console.print("\nRetry with: [bold]dotfit push --retry-failed[/bold]")


# ── Helpers ───────────────────────────────────────────────────────────


def _make_strava_client(settings) -> tuple[StravaClient, RateLimiter]:
    if not settings.strava_client_id or not settings.strava_client_secret:
        console.print("[red]Error:[/red] Set STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET in .env")
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
