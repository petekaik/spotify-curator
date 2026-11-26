"""Hermes tools for spotify-curator.

These are the 6 tools exposed to other Hermes agents when this package
is loaded as a skill. They wrap the CLI commands and return structured
JSON for downstream consumption (TTS, other tools, etc.).

Usage from a Hermes session:
    from src.hermes_skill.tools import (
        spotify_curator_status,
        spotify_curator_refresh_profile,
        spotify_curator_discover,
        spotify_curator_generate_mood,
        spotify_curator_generate_weekly,
        spotify_curator_get_reports,
    )

All tools return a JSON-serializable dict. Failures return
`{"error": "...", "details": "..."}` instead of raising.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any

log = logging.getLogger(__name__)


# Lazy imports to avoid loading the whole package on Hermes startup
def _import_spotify_curator() -> dict[str, Any]:
    """Import the spotify-curator modules lazily (keeps tool registration cheap).

    Returns an explicit dict: a `return locals()` version lost its imports to a lint autofix.
    """
    from src.analyzer.cache import PROFILE_PATH, load_profile, profile_age_hours, profile_exists
    from src.analyzer.profile import ProfileBuilder
    from src.discovery.ranking import (
        Candidate,
        deduplicate_candidates,
        load_tuning,
        rank_artists,
    )
    from src.discovery.sources.lastfm import LastfmClient
    from src.discovery.sources.listenbrainz import ListenBrainzClient
    from src.playlist.builder import PlaylistBuilder
    from src.spotify.api_v2 import SpotifyAPIv2
    from src.spotify.auth import get_current_user_id, get_spotify_client, is_authenticated

    return {
        "PROFILE_PATH": PROFILE_PATH,
        "load_profile": load_profile,
        "profile_age_hours": profile_age_hours,
        "profile_exists": profile_exists,
        "ProfileBuilder": ProfileBuilder,
        "Candidate": Candidate,
        "deduplicate_candidates": deduplicate_candidates,
        "load_tuning": load_tuning,
        "rank_artists": rank_artists,
        "LastfmClient": LastfmClient,
        "ListenBrainzClient": ListenBrainzClient,
        "PlaylistBuilder": PlaylistBuilder,
        "SpotifyAPIv2": SpotifyAPIv2,
        "get_current_user_id": get_current_user_id,
        "get_spotify_client": get_spotify_client,
        "is_authenticated": is_authenticated,
    }


# ────────────────────────────────────────────────────────────
# Tool 1: status
# ────────────────────────────────────────────────────────────


def spotify_curator_status() -> dict[str, Any]:
    """Check authentication, profile, and configuration status.

    Returns:
        dict with:
          - authenticated: bool
          - user_id: str or None
          - profile_built: bool
          - profile_age_hours: float or None
          - lastfm_configured: bool
    """
    try:
        mods = _import_spotify_curator()

        authenticated = mods["is_authenticated"]()
        user_id = mods["get_current_user_id"]() if authenticated else None

        profile_built = mods["profile_exists"](mods["PROFILE_PATH"])
        profile_age = mods["profile_age_hours"](mods["PROFILE_PATH"]) if profile_built else None

        # Check Last.fm key (env var, no API call)
        import os

        lastfm_key = os.getenv("LASTFM_API_KEY", "")
        lastfm_configured = bool(lastfm_key and len(lastfm_key) > 5)

        return {
            "authenticated": authenticated,
            "user_id": user_id,
            "profile_built": profile_built,
            "profile_age_hours": profile_age,
            "lastfm_configured": lastfm_configured,
            "tools_healthy": authenticated and lastfm_configured,
        }
    except Exception as e:
        log.exception("status check failed")
        return {"error": "status_check_failed", "details": str(e)}


# ────────────────────────────────────────────────────────────
# Tool 2: refresh_profile
# ────────────────────────────────────────────────────────────


def spotify_curator_refresh_profile(
    include_audio_features: bool = True,
) -> dict[str, Any]:
    """Rebuild the user's taste profile from Spotify.

    Args:
        include_audio_features: fetch Spotify audio features (tempo/energy/valence).
            Slower but improves ranking quality. Set to False if the deprecated
            endpoint is failing.

    Returns:
        dict with stats about the freshly built profile.
    """
    start = time.time()
    try:
        mods = _import_spotify_curator()

        if not mods["is_authenticated"]():
            return {
                "error": "not_authenticated",
                "details": "Run deploy/scripts/auth_on_host.sh on host Mac first.",
            }

        sp = mods["get_spotify_client"]()
        api = mods["SpotifyAPIv2"](sp)
        builder = mods["ProfileBuilder"](api)

        profile = builder.build_and_save(include_features=include_audio_features)
        duration = time.time() - start

        return {
            "success": True,
            "user_id": profile.user_id,
            "artists_count": profile.total_artists,
            "genres_count": profile.total_genres,
            "tracks_count": profile.total_tracks,
            "top_genres": [g for g, _ in profile.top_genres(5)],
            "top_artists": [a for a, _ in profile.top_artists(3)],
            "duration_seconds": round(duration, 1),
        }
    except Exception as e:
        log.exception("refresh_profile failed")
        return {"error": "profile_build_failed", "details": str(e)}


# ────────────────────────────────────────────────────────────
# Tool 3: discover
# ────────────────────────────────────────────────────────────


def spotify_curator_discover(
    tag: str = "indie rock",
    period: str = "6month",
    limit: int = 30,
    use_listenbrainz: bool = True,
) -> dict[str, Any]:
    """Find and rank emerging artists. Does NOT write to Spotify.

    Args:
        tag: Last.fm tag to search
        period: "overall" | "7day" | "1month" | "3month" | "6month" | "12month"
        limit: max candidates to return (1-100)
        use_listenbrainz: also use ListenBrainz ML-similar for cross-validation

    Returns:
        dict with ranked candidates, scores, and component breakdown
    """
    try:
        mods = _import_spotify_curator()

        if not mods["profile_exists"](mods["PROFILE_PATH"]):
            return {
                "error": "profile_not_built",
                "details": "Call spotify_curator_refresh_profile first.",
            }

        profile = mods["load_profile"](mods["PROFILE_PATH"])
        if profile is None:
            return {"error": "profile_load_failed", "details": "Cache file unreadable."}

        lfm = mods["LastfmClient"]()
        if not lfm._api_key:
            return {"error": "lastfm_not_configured", "details": "Set LASTFM_API_KEY in .env"}

        artists = lfm.tag_get_top_artists(tag, period=period, limit=limit)
        if not artists:
            return {
                "tag": tag,
                "period": period,
                "candidates_count": 0,
                "ranked": [],
                "error": "no_results",
            }

        candidates = [mods["Candidate"].from_lastfm(a, source=f"lastfm:tag:{tag}") for a in artists]
        candidates = mods["deduplicate_candidates"](candidates)
        ranked = mods["rank_artists"](candidates, profile, limit=limit)

        return {
            "tag": tag,
            "period": period,
            "candidates_count": len(candidates),
            "ranked": [
                {
                    "rank": i + 1,
                    "name": cand.name,
                    "score": round(score, 4),
                    "lastfm_listeners": cand.lastfm_listeners,
                    "tags": cand.lastfm_tags[:5],
                    "score_breakdown": {k: round(v, 4) for k, v in components.items()},
                }
                for i, (cand, score, components) in enumerate(ranked)
            ],
        }
    except Exception as e:
        log.exception("discover failed")
        return {"error": "discover_failed", "details": str(e)}


# ────────────────────────────────────────────────────────────
# Tool 4: generate_mood
# ────────────────────────────────────────────────────────────

from src.discovery.ranking import DEFAULT_MOOD_TAGS  # noqa: E402

VALID_MOODS = tuple(DEFAULT_MOOD_TAGS)


def spotify_curator_generate_mood(
    mood: str,
    n_artists: int = 15,
    tracks_per_artist: int = 2,
    name: str | None = None,
    public: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Build a single-mood playlist and write to Spotify (or only preview it with dry_run).

    Args:
        mood: one of: cheerful, calming, stimulating, focus, melancholic, energetic
        n_artists: how many artists to consider (1-50)
        tracks_per_artist: 1-5, 2-3 recommended
        name: override the auto-generated playlist name
        public: make playlist public
        dry_run: build and return the track list without writing to Spotify

    Returns:
        dict with playlist URL, name, and stats
    """
    if mood not in VALID_MOODS:
        return {"error": "invalid_mood", "details": f"mood must be one of {VALID_MOODS}"}

    try:
        mods = _import_spotify_curator()

        if not mods["is_authenticated"]():
            return {
                "error": "not_authenticated",
                "details": "Run deploy/scripts/auth_on_host.sh on host Mac first.",
            }
        if not mods["profile_exists"](mods["PROFILE_PATH"]):
            return {
                "error": "profile_not_built",
                "details": "Call spotify_curator_refresh_profile first.",
            }

        profile = mods["load_profile"](mods["PROFILE_PATH"])

        # Use a tag hint per mood to bias discovery toward that aesthetic
        tag = mods["load_tuning"]().mood_tags[mood]  # tunable via update_tuning

        lfm = mods["LastfmClient"]()
        if not lfm._api_key:
            return {"error": "lastfm_not_configured", "details": "Set LASTFM_API_KEY in .env"}

        artists = lfm.tag_get_top_artists(tag, period="6month", limit=n_artists * 2)
        if not artists:
            return {"error": "no_artists_found", "details": f"No artists for tag {tag}"}

        candidates = [mods["Candidate"].from_lastfm(a, source=f"lastfm:tag:{tag}") for a in artists]
        candidates = mods["deduplicate_candidates"](candidates)
        ranked = mods["rank_artists"](candidates, profile, limit=n_artists)

        sp = mods["get_spotify_client"]()
        api = mods["SpotifyAPIv2"](sp)
        builder = mods["PlaylistBuilder"](api)

        if name is None:
            week = datetime.now().strftime("%Y-W%V")
            name = f"Weekly {mood.capitalize()} — {week}"

        playlist = builder.build(
            ranked,
            name=name,
            description=f"Auto-generated by spotify-curator (mood: {mood})",
            tracks_per_artist=tracks_per_artist,
        )
        if not dry_run:
            playlist = builder.write_to_spotify(playlist, public=public)

        return {
            "success": True,
            "mood": mood,
            "tag": tag,
            "dry_run": dry_run,
            "playlist_url": None
            if dry_run
            else f"https://open.spotify.com/playlist/{playlist.spotify_playlist_id}",
            "playlist_name": playlist.name,
            "artists_count": playlist.total_artist_count,
            "tracks_count": len(playlist.tracks),
            "artists": list(dict.fromkeys(t.artist_name for t in playlist.tracks)),
            "tracks": [f"{t.artist_name} — {t.name}" for t in playlist.tracks] if dry_run else None,
            "summary": (
                f"{playlist.total_artist_count} emerging artists across "
                f"{tag} and related tags. "
                f"Skipped {playlist.skipped_no_spotify} (no Spotify match) and "
                f"{playlist.skipped_no_tracks} (no top tracks)."
            ),
        }
    except Exception as e:
        log.exception("generate_mood failed")
        return {"error": "generate_mood_failed", "details": str(e)}


# ────────────────────────────────────────────────────────────
# Tool 5: generate_weekly
# ────────────────────────────────────────────────────────────


def spotify_curator_generate_weekly(
    n_artists_per_mood: int = 15,
    tracks_per_artist: int = 2,
    public: bool = False,
    refresh_profile_first: bool = False,
) -> dict[str, Any]:
    """Full weekly digest: one playlist per mood, written to Spotify.

    Args:
        n_artists_per_mood: artists to consider per mood
        tracks_per_artist: 1-5, 2-3 recommended
        public: make all playlists public
        refresh_profile_first: if True, rebuild profile before generating (slower)

    Returns:
        dict with all 6 mood-playlists' URLs and stats
    """
    try:
        if refresh_profile_first:
            result = spotify_curator_refresh_profile()
            if "error" in result:
                return result

        playlists = []
        total_artists = 0
        total_tracks = 0
        for mood in VALID_MOODS:
            r = spotify_curator_generate_mood(
                mood=mood,
                n_artists=n_artists_per_mood,
                tracks_per_artist=tracks_per_artist,
                public=public,
            )
            if "error" in r:
                log.warning(f"Mood {mood} failed: {r['error']}")
                continue
            playlists.append(
                {
                    "mood": mood,
                    "url": r["playlist_url"],
                    "name": r["playlist_name"],
                    "tracks": r["tracks_count"],
                }
            )
            total_artists += r["artists_count"]
            total_tracks += r["tracks_count"]

        return {
            "success": True,
            "playlists": playlists,
            "total_artists": total_artists,
            "total_tracks": total_tracks,
            "duration_seconds": None,  # could track this
        }
    except Exception as e:
        log.exception("generate_weekly failed")
        return {"error": "generate_weekly_failed", "details": str(e)}


# ────────────────────────────────────────────────────────────
# Tool 6: get_reports
# ────────────────────────────────────────────────────────────


def spotify_curator_get_reports(
    limit: int = 10,
    mood_filter: str | None = None,
) -> dict[str, Any]:
    """List past weekly runs and their Spotify playlist URLs.

    Args:
        limit: most recent N runs
        mood_filter: only return runs containing this mood

    Returns:
        dict with list of past runs

    NOTE: This requires a `reports/` directory under SPOTIFY_CURATOR_HOME,
          which is currently only written by the planned daemon (FP-9).
          For v0.1.0/v0.2.0, this returns an empty list unless the
          user has manually saved reports.
    """
    try:
        from src.config import DATA_DIR

        reports_dir = DATA_DIR / "reports"
        if not reports_dir.exists():
            return {
                "runs": [],
                "details": "No reports directory yet. Reports are saved by the daemon (FP-9).",
            }

        runs = []
        for run_dir in sorted(reports_dir.iterdir(), reverse=True)[:limit]:
            if not run_dir.is_dir():
                continue
            meta_file = run_dir / "playlists.json"
            if not meta_file.exists():
                continue
            try:
                meta = json.loads(meta_file.read_text())
                if mood_filter and not any(
                    p.get("mood") == mood_filter for p in meta.get("playlists", [])
                ):
                    continue
                runs.append(
                    {
                        "id": run_dir.name,
                        "generated_at": meta.get("generated_at"),
                        "playlists": meta.get("playlists", []),
                    }
                )
            except (json.JSONDecodeError, OSError):
                continue
        return {"runs": runs}
    except Exception as e:
        log.exception("get_reports failed")
        return {"error": "get_reports_failed", "details": str(e)}


# ────────────────────────────────────────────────────────────
# Tool registry (for hermes-agent auto-discovery)
# ────────────────────────────────────────────────────────────

# ────────────────────────────────────────────────────────────
# Tools 7-8: tuning (read and change how ranking behaves)
# ────────────────────────────────────────────────────────────

TUNING_HELP = {
    "genre_match": "Weight of genre overlap with your profile (0-1).",
    "emerging_signal": "Weight of a small Last.fm audience (0-1).",
    "feature_match": "Weight of audio-feature similarity (0-1; mostly inactive, Spotify deprecated it).",
    "discovery_bonus": "Weight of having few albums (0-1).",
    "geo_bonus": "Weight of matching preferred_geo_tags (0-1).",
    "mainstream_penalty": "Penalty above the listener threshold (-1-0; more negative = more obscure).",
    "mainstream_listener_threshold": "Last.fm listeners above which the penalty applies.",
    "penalty_steepness": "0 = flat penalty above threshold, 1 = grows with audience size.",
    "discovery_album_full_bonus": "Album count that still gets the full discovery bonus.",
    "discovery_album_zero_bonus": "Album count at which the discovery bonus reaches zero.",
    "preferred_geo_tags": "Local-scene tags that earn geo_bonus, e.g. ['finnish'].",
    "mood_tags": "Last.fm tag that seeds each mood playlist.",
}


def spotify_curator_get_tuning() -> dict[str, Any]:
    """Current ranking tuning, the defaults, and what each setting does."""
    from dataclasses import asdict

    from src.discovery.ranking import TUNING_PATH, TuningConfig, load_tuning

    try:
        return {
            "tuning": asdict(load_tuning()),
            "defaults": asdict(TuningConfig()),
            "help": TUNING_HELP,
            "saved_in": str(TUNING_PATH) if TUNING_PATH.exists() else None,
        }
    except Exception as e:
        return {"error": "tuning_invalid", "details": str(e)}


def spotify_curator_update_tuning(
    changes: dict[str, Any] | None = None, reset: bool = False
) -> dict[str, Any]:
    """Change ranking tuning; applies to every later discovery and playlist run.

    Args:
        changes: partial settings, e.g. {"mainstream_penalty": -0.5, "mood_tags": {"focus": "post-rock"}}
        reset: restore all defaults first (then apply changes, if any)
    """
    from dataclasses import asdict

    from src.discovery.ranking import update_tuning

    try:
        cfg = update_tuning(changes or {}, reset=reset)
        return {"success": True, "tuning": asdict(cfg)}
    except (ValueError, TypeError) as e:
        return {"error": "invalid_tuning", "details": str(e)}


TOOL_REGISTRY = {
    "spotify_curator_status": {
        "function": spotify_curator_status,
        "schema": {
            "name": "spotify_curator_status",
            "description": "Check authentication, profile, and config status.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    "spotify_curator_refresh_profile": {
        "function": spotify_curator_refresh_profile,
        "schema": {
            "name": "spotify_curator_refresh_profile",
            "description": "Rebuild user taste profile from Spotify.",
            "parameters": {
                "type": "object",
                "properties": {
                    "include_audio_features": {
                        "type": "boolean",
                        "default": True,
                        "description": "Fetch Spotify audio features (tempo/energy/valence).",
                    },
                },
                "required": [],
            },
        },
    },
    "spotify_curator_discover": {
        "function": spotify_curator_discover,
        "schema": {
            "name": "spotify_curator_discover",
            "description": "Find and rank emerging artists by tag. Does NOT write to Spotify.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tag": {"type": "string", "default": "indie rock"},
                    "period": {
                        "type": "string",
                        "default": "6month",
                        "enum": ["overall", "7day", "1month", "3month", "6month", "12month"],
                    },
                    "limit": {"type": "integer", "default": 30, "minimum": 1, "maximum": 100},
                    "use_listenbrainz": {"type": "boolean", "default": True},
                },
                "required": [],
            },
        },
    },
    "spotify_curator_generate_mood": {
        "function": spotify_curator_generate_mood,
        "schema": {
            "name": "spotify_curator_generate_mood",
            "description": "Build a single-mood playlist and write to Spotify.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mood": {
                        "type": "string",
                        "enum": list(VALID_MOODS),
                        "description": "Target mood for the playlist.",
                    },
                    "n_artists": {"type": "integer", "default": 15, "minimum": 1, "maximum": 50},
                    "tracks_per_artist": {
                        "type": "integer",
                        "default": 2,
                        "minimum": 1,
                        "maximum": 5,
                    },
                    "name": {
                        "type": "string",
                        "description": "Override auto-generated playlist name.",
                    },
                    "public": {"type": "boolean", "default": False},
                    "dry_run": {
                        "type": "boolean",
                        "default": False,
                        "description": "Preview without writing to Spotify.",
                    },
                },
                "required": ["mood"],
            },
        },
    },
    "spotify_curator_generate_weekly": {
        "function": spotify_curator_generate_weekly,
        "schema": {
            "name": "spotify_curator_generate_weekly",
            "description": "Full weekly digest: 6 playlists (one per mood) written to Spotify.",
            "parameters": {
                "type": "object",
                "properties": {
                    "n_artists_per_mood": {
                        "type": "integer",
                        "default": 15,
                        "minimum": 1,
                        "maximum": 50,
                    },
                    "tracks_per_artist": {
                        "type": "integer",
                        "default": 2,
                        "minimum": 1,
                        "maximum": 5,
                    },
                    "public": {"type": "boolean", "default": False},
                    "refresh_profile_first": {"type": "boolean", "default": False},
                },
                "required": [],
            },
        },
    },
    "spotify_curator_get_reports": {
        "function": spotify_curator_get_reports,
        "schema": {
            "name": "spotify_curator_get_reports",
            "description": "List past weekly runs and their Spotify URLs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 100},
                    "mood_filter": {
                        "type": "string",
                        "description": "Only return runs containing this mood.",
                    },
                },
                "required": [],
            },
        },
    },
    "spotify_curator_get_tuning": {
        "function": spotify_curator_get_tuning,
        "schema": {
            "name": "spotify_curator_get_tuning",
            "description": "Show ranking tuning, defaults and what each setting does.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    "spotify_curator_update_tuning": {
        "function": spotify_curator_update_tuning,
        "schema": {
            "name": "spotify_curator_update_tuning",
            "description": "Change ranking tuning (partial). Applies to all later runs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "changes": {
                        "type": "object",
                        "description": 'Settings to change, e.g. {"mainstream_penalty": -0.5}.',
                    },
                    "reset": {"type": "boolean", "default": False},
                },
                "required": [],
            },
        },
    },
}
