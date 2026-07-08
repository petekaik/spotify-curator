"""CLI entry point for Spotify Curator.

Built with Typer (modern, type-safe CLI library that wraps Click).

Commands:
  auth        — Authenticate with Spotify (OAuth browser flow)
  profile     — Build/show the user profile
  discover    — Find emerging artists (preview, no Spotify write)
  playlist    — Build and write a playlist to Spotify
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from src.spotify.auth import (
    get_spotify_client,
    is_authenticated,
    get_current_user_id,
)
from src.spotify.api_v2 import SpotifyAPIv2
from src.analyzer.profile import ProfileBuilder
from src.analyzer.cache import profile_exists, profile_age_hours, load_profile, PROFILE_PATH
from src.discovery.sources.lastfm import LastfmClient
from src.discovery.sources.listenbrainz import ListenBrainzClient
from src.discovery.ranking import (
    Candidate,
    TuningConfig,
    rank_artists,
    deduplicate_candidates,
)
from src.playlist.builder import PlaylistBuilder

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("spotify-curator")

# Typer app + Rich console
app = typer.Typer(
    name="spotify-curator",
    help="Find emerging artists and build Spotify playlists.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()

# Sub-command groups
profile_app = typer.Typer(help="Manage user taste profile")
discover_app = typer.Typer(help="Discover emerging artists")
playlist_app = typer.Typer(help="Build and write playlists")

app.add_typer(profile_app, name="profile")
app.add_typer(discover_app, name="discover")
app.add_typer(playlist_app, name="playlist")


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────

def _get_api() -> SpotifyAPIv2:
    """Get authenticated Spotify API wrapper, with friendly error."""
    if not is_authenticated():
        console.print(
            "[red]Not authenticated.[/red] Run: [bold]spotify-curator auth[/bold]"
        )
        raise typer.Exit(code=1)
    sp = get_spotify_client()
    return SpotifyAPIv2(sp)


def _load_profile_or_exit() -> "object":  # type: ignore
    """Load cached profile or exit with friendly error."""
    if not profile_exists(PROFILE_PATH):
        console.print(
            "[red]No profile found.[/red] Run: [bold]spotify-curator profile build[/bold]"
        )
        raise typer.Exit(code=1)
    profile = load_profile(PROFILE_PATH)
    if profile is None:
        console.print("[red]Failed to load profile.[/red]")
        raise typer.Exit(code=1)
    return profile


# ────────────────────────────────────────────────────────────
# Top-level commands
# ────────────────────────────────────────────────────────────

@app.command()
def auth():
    """Authenticate with Spotify (opens browser for OAuth flow)."""
    console.print("[bold]Authenticating with Spotify...[/bold]")
    with console.status("Waiting for browser..."):
        try:
            sp = get_spotify_client()
            user = sp.current_user()
            console.print(
                f"[green]✓ Authenticated as:[/green] "
                f"{user.get('display_name', '?')} ({user['id']})"
            )
        except Exception as e:
            console.print(f"[red]✗ Authentication failed:[/red] {e}")
            raise typer.Exit(code=1)


@app.command()
def status():
    """Show current authentication and profile status."""
    table = Table(title="Spotify Curator Status")
    table.add_column("Item", style="cyan")
    table.add_column("Status", style="green")

    # Auth
    if is_authenticated():
        uid = get_current_user_id()
        table.add_row("Authentication", f"✓ {uid}")
    else:
        table.add_row("Authentication", "✗ Not authenticated")

    # Profile
    if profile_exists(PROFILE_PATH):
        age = profile_age_hours(PROFILE_PATH)
        age_str = f"{age:.1f} hours ago" if age is not None else "?"
        table.add_row("Profile cache", f"✓ Built {age_str}")
    else:
        table.add_row("Profile cache", "✗ Not built")

    console.print(table)


# ────────────────────────────────────────────────────────────
# profile subcommands
# ────────────────────────────────────────────────────────────

@profile_app.command("build")
def profile_build(
    no_features: bool = typer.Option(False, "--no-features", help="Skip audio features (faster)"),
):
    """Build or refresh the user taste profile from Spotify."""
    api = _get_api()
    builder = ProfileBuilder(api)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        progress.add_task("Fetching top artists and tracks...", total=None)
        progress.add_task("Computing weights...", total=None)
        try:
            profile = builder.build_and_save(include_features=not no_features)
        except Exception as e:
            console.print(f"[red]Profile build failed:[/red] {e}")
            raise typer.Exit(code=1)

    console.print(
        f"[green]✓ Profile built:[/green] "
        f"{profile.total_artists} artists, "
        f"{profile.total_genres} genres, "
        f"{profile.total_tracks} tracks"
    )


@profile_app.command("show")
def profile_show(
    top_n: int = typer.Option(10, "--top", "-n", help="Show top N genres/artists"),
):
    """Show the cached user profile."""
    profile = _load_profile_or_exit()

    # Genres
    console.print(f"\n[bold cyan]Top {top_n} genres[/bold cyan]")
    genre_table = Table()
    genre_table.add_column("Genre", style="cyan")
    genre_table.add_column("Weight", style="green")
    for genre, weight in profile.top_genres(top_n):
        bar = "█" * int(weight * 50)
        genre_table.add_row(genre, f"{weight:.3f}  {bar}")
    console.print(genre_table)

    # Artists
    console.print(f"\n[bold cyan]Top {top_n} artists[/bold cyan]")
    artist_table = Table()
    artist_table.add_column("Artist ID", style="dim")
    artist_table.add_column("Weight", style="green")
    for artist_id, weight in profile.top_artists(top_n):
        artist_table.add_row(artist_id, f"{weight:.3f}")
    console.print(artist_table)

    # Features
    if profile.features_centroid:
        fc = profile.features_centroid
        console.print(f"\n[bold cyan]Audio features centroid[/bold cyan]")
        console.print(
            f"  tempo={fc.tempo:.1f}  energy={fc.energy:.2f}  "
            f"valence={fc.valence:.2f}  acousticness={fc.acousticness:.2f}"
        )


# ────────────────────────────────────────────────────────────
# discover subcommands
# ────────────────────────────────────────────────────────────

@discover_app.command("lastfm")
def discover_lastfm(
    tag: str = typer.Option("indie rock", "--tag", "-t", help="Tag to search"),
    period: str = typer.Option("6month", "--period", "-p", help="overall|7day|1month|3month|6month|12month"),
    limit: int = typer.Option(30, "--limit", "-l", help="Max results"),
    rank: bool = typer.Option(True, "--rank/--no-rank", help="Apply ranking algorithm"),
):
    """Find emerging artists from Last.fm by tag."""
    client = LastfmClient()
    if not client._api_key:
        console.print("[red]LASTFM_API_KEY not set in .env[/red]")
        raise typer.Exit(code=1)

    with console.status(f"Fetching top '{tag}' artists for {period}..."):
        artists = client.tag_get_top_artists(tag, period=period, limit=limit)

    if not artists:
        console.print(f"[red]No artists found for tag '{tag}'[/red]")
        raise typer.Exit(code=1)

    console.print(f"[green]Found {len(artists)} artists[/green]")

    if rank and artists:
        profile = _load_profile_or_exit()
        candidates = [Candidate.from_lastfm(a, source=f"lastfm:tag:{tag}") for a in artists]
        candidates = deduplicate_candidates(candidates)

        with console.status("Ranking against your profile..."):
            ranked = rank_artists(candidates, profile, limit=20)

        # Display top 20
        table = Table(title=f"Top {min(20, len(ranked))} for you")
        table.add_column("#", style="dim")
        table.add_column("Artist", style="cyan")
        table.add_column("Score", style="green")
        table.add_column("Listeners", style="dim")
        table.add_column("Tags", style="dim")

        for i, (cand, score, _components) in enumerate(ranked[:20], 1):
            table.add_row(
                str(i),
                cand.name,
                f"{score:.3f}",
                f"{cand.lastfm_listeners:,}",
                ", ".join(cand.lastfm_tags[:3]),
            )
        console.print(table)
    else:
        # No ranking, just show raw list
        for a in artists[:20]:
            console.print(f"  {a.name}  ({a.listeners:,} listeners)")


@discover_app.command("listenbrainz")
def discover_listenbrainz(
    limit: int = typer.Option(10, "--limit", "-l"),
):
    """Find similar artists using ListenBrainz ML."""
    profile = _load_profile_or_exit()

    # Get top artists' MBIDs from profile
    # For now, just sample from saved_album_ids (would need a MusicBrainz lookup)
    # TODO: add MusicBrainz MBID resolution from Spotify IDs
    console.print(
        "[yellow]Note:[/yellow] ListenBrainz needs MBIDs. "
        "For now, the Spotify→MBID mapping is not implemented in v0.1.0."
    )
    console.print(
        "This command will work fully once MusicBrainz integration is added (FP-3b)."
    )


# ────────────────────────────────────────────────────────────
# playlist subcommands
# ────────────────────────────────────────────────────────────

@playlist_app.command("create")
def playlist_create(
    name: str = typer.Argument(..., help="Playlist name"),
    description: str = typer.Option("", "--description", "-d", help="Playlist description"),
    tag: str = typer.Option("indie rock", "--tag", "-t", help="Last.fm tag to search"),
    period: str = typer.Option("6month", "--period", "-p", help="Last.fm time period"),
    n_artists: int = typer.Option(30, "--n-artists", "-n", help="How many artists to consider"),
    tracks_per_artist: int = typer.Option(2, "--tracks", help="Tracks per artist"),
    public: bool = typer.Option(False, "--public/--private", help="Make playlist public"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Build but don't write to Spotify"),
):
    """Build a playlist from emerging artists and write to Spotify."""
    api = _get_api()
    profile = _load_profile_or_exit()

    # Step 1: Get candidates from Last.fm
    lfm = LastfmClient()
    if not lfm._api_key:
        console.print("[red]LASTFM_API_KEY not set in .env[/red]")
        raise typer.Exit(code=1)

    with console.status(f"Fetching Last.fm '{tag}' artists ({period})..."):
        artists = lfm.tag_get_top_artists(tag, period=period, limit=n_artists * 2)

    if not artists:
        console.print(f"[red]No artists found for tag '{tag}'[/red]")
        raise typer.Exit(code=1)

    candidates = [Candidate.from_lastfm(a, source=f"lastfm:tag:{tag}") for a in artists]
    candidates = deduplicate_candidates(candidates)

    # Step 2: Rank
    with console.status("Ranking against your profile..."):
        ranked = rank_artists(candidates, profile, limit=n_artists)

    console.print(f"[green]Ranked {len(ranked)} candidates[/green]")

    # Step 3: Build playlist
    builder = PlaylistBuilder(api)
    with console.status("Resolving to Spotify IDs and fetching tracks..."):
        playlist = builder.build(
            ranked,
            name=name,
            description=description,
            tracks_per_artist=tracks_per_artist,
        )

    console.print(
        f"[green]Built playlist:[/green] {len(playlist.tracks)} tracks from "
        f"{playlist.total_artist_count} artists "
        f"(skipped {playlist.skipped_no_spotify} unresolved, "
        f"{playlist.skipped_no_tracks} no-tracks)"
    )

    if dry_run:
        console.print("[yellow]--dry-run: not writing to Spotify[/yellow]")
        return

    # Step 4: Write to Spotify
    with console.status("Creating Spotify playlist and adding tracks..."):
        playlist = builder.write_to_spotify(playlist, public=public)

    playlist_url = f"https://open.spotify.com/playlist/{playlist.spotify_playlist_id}"
    console.print(f"[green]✓ Playlist created:[/green] {playlist_url}")


# ────────────────────────────────────────────────────────────
# Entry point
# ────────────────────────────────────────────────────────────

def main():
    """Main entry point for `python -m src.cli.main` or `spotify-curator`."""
    try:
        app()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(130)


if __name__ == "__main__":
    main()
