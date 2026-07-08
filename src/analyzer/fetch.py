"""Fetch user listening history from Spotify.

Wraps the multiple endpoints needed to build a user profile:
- /me/top/artists (3 time ranges)
- /me/top/tracks (3 time ranges)
- /me/library (saved albums + tracks, NEW endpoint)
- /audio-features (track features, DEPRECATED but still works as of 2026-07)

All requests go through our api_v2 wrapper to ensure we use the correct
Feb 2026 endpoints.
"""
from __future__ import annotations

import logging
from typing import Optional

from src.spotify.api_v2 import SpotifyAPIv2
from src.analyzer.types import ArtistRef, TrackRef, TimeRangeData

log = logging.getLogger(__name__)

# Spotify's official time-range identifiers
TIME_RANGES = ("short_term", "medium_term", "long_term")
TIME_RANGE_WEIGHTS = {
    "short_term": 0.5,   # last 4 weeks — most important
    "medium_term": 0.3,  # last 6 months — stable preferences
    "long_term": 0.2,    # all time — foundational taste
}


def fetch_top_artists(
    api: SpotifyAPIv2,
    time_range: str,
    limit: int = 50,
) -> list[ArtistRef]:
    """Fetch user's top artists for a time range.

    NOTE: spotipy 2.26 still has current_user_top_artists on the OLD path,
    which still works because this endpoint wasn't changed in Feb 2026.
    We use it directly via spotipy's _get.

    Args:
        api: SpotifyAPIv2 wrapper (we use its underlying spotipy client)
        time_range: "short_term" | "medium_term" | "long_term"
        limit: max 50 (Spotify's hard cap)

    Returns:
        list of ArtistRef, ordered by Spotify's ranking
    """
    if time_range not in TIME_RANGES:
        raise ValueError(f"Invalid time_range: {time_range}")

    # Endpoint: GET /me/top/artists
    # spotipy uses ._get which handles auth + base URL
    result = api._sp._get(
        "me/top/artists",
        time_range=time_range,
        limit=min(limit, 50),
    ) or {}

    artists = []
    for item in result.get("items", []):
        # In Feb 2026, item.popularity was REMOVED for /artists/{id}
        # but for /me/top/artists it may still be present (it was a
        # documented field on the artist object overall). Defensive:
        popularity = item.get("popularity")  # may be None
        # Defensive: followers may be int (if test data simplified) or dict
        followers_raw = item.get("followers", 0)
        if isinstance(followers_raw, dict):
            followers = followers_raw.get("total", 0)
        else:
            followers = int(followers_raw or 0)
        # Defensive: genres may be missing
        genres = item.get("genres") or []
        artists.append(ArtistRef(
            id=item["id"],
            name=item["name"],
            genres=genres,
            popularity=popularity,
            followers=followers,
        ))
    return artists


def fetch_top_tracks(
    api: SpotifyAPIv2,
    time_range: str,
    limit: int = 50,
) -> list[TrackRef]:
    """Fetch user's top tracks for a time range.

    Args:
        api: SpotifyAPIv2 wrapper
        time_range: "short_term" | "medium_term" | "long_term"
        limit: max 50

    Returns:
        list of TrackRef, ordered by Spotify's ranking
    """
    if time_range not in TIME_RANGES:
        raise ValueError(f"Invalid time_range: {time_range}")

    result = api._sp._get(
        "me/top/tracks",
        time_range=time_range,
        limit=min(limit, 50),
    ) or {}

    tracks = []
    for item in result.get("items", []):
        artist_ids = [a["id"] for a in item.get("artists", [])]
        artist_names = [a["name"] for a in item.get("artists", [])]
        album = item.get("album", {}) or {}
        # Track popularity still exists (only artist popularity removed)
        tracks.append(TrackRef(
            id=item["id"],
            name=item["name"],
            uri=item.get("uri", f"spotify:track:{item['id']}"),
            artist_ids=artist_ids,
            artist_names=artist_names,
            album_id=album.get("id"),
            album_name=album.get("name"),
            duration_ms=item.get("duration_ms", 0),
            popularity=item.get("popularity", 0),
        ))
    return tracks


def fetch_saved_albums(
    api: SpotifyAPIv2,
    limit: int = 50,
) -> list[str]:
    """Fetch IDs of albums the user has saved in their library.

    Uses the NEW /me/library endpoint (Feb 2026).

    Returns:
        list of album IDs
    """
    try:
        # Try the NEW endpoint first
        result = api._sp._get(
            "me/library",
            type="album",
            limit=min(limit, 50),
        )
        # NEW endpoint returns 'items' or 'albums' depending on doc version
        items = result.get("items", result.get("albums", []))
        return [item.get("album", item).get("id") for item in items if item.get("album") or item.get("id")]
    except Exception as e:
        # Fall back to old endpoint (deprecated but might still work)
        log.warning(f"NEW /me/library failed ({e}), falling back to old /me/albums")
        try:
            result = api._sp._get("me/albums", limit=min(limit, 50))
            return [item["album"]["id"] for item in result.get("items", [])]
        except Exception as e2:
            log.error(f"Both endpoints failed for saved albums: {e2}")
            return []


def fetch_audio_features(
    api: SpotifyAPIv2,
    track_ids: list[str],
) -> dict[str, dict]:
    """Fetch audio features for a list of tracks.

    NOTE: Spotify deprecated this endpoint Nov 2024. It still works as of
    mid-2026 but is on borrowed time. Returns empty dict on failure.

    Returns:
        dict of track_id → features dict with tempo, energy, valence, etc.
    """
    if not track_ids:
        return {}

    try:
        # Endpoint: GET /audio-features?ids=...
        result = api._sp._get("audio-features", ids=",".join(track_ids[:100])) or {}
        features_by_id = {}
        for feat in result.get("audio_features", []):
            if feat is None:
                continue
            features_by_id[feat["id"]] = feat
        return features_by_id
    except Exception as e:
        log.warning(f"audio-features failed (deprecated endpoint): {e}")
        return {}


def fetch_all_for_profile(
    api: SpotifyAPIv2,
    include_features: bool = True,
) -> dict[str, TimeRangeData]:
    """Fetch all data needed to build a user profile.

    Returns:
        dict mapping time_range → TimeRangeData (with artists + tracks populated)
    """
    result: dict[str, TimeRangeData] = {}

    for tr in TIME_RANGES:
        log.info(f"Fetching top data for {tr}...")
        artists = fetch_top_artists(api, tr, limit=50)
        tracks = fetch_top_tracks(api, tr, limit=50)
        result[tr] = TimeRangeData(
            time_range=tr,
            weight=TIME_RANGE_WEIGHTS[tr],
            artists=artists,
            tracks=tracks,
        )

    if include_features:
        # Fetch features for the top tracks across all time ranges
        all_track_ids = []
        for tr in TIME_RANGES:
            all_track_ids.extend(t.id for t in result[tr].tracks)
        # Dedupe
        all_track_ids = list(set(all_track_ids))[:100]
        log.info(f"Fetching audio features for {len(all_track_ids)} tracks...")
        features = fetch_audio_features(api, all_track_ids)
        # Store on the tracks themselves? Or compute centroid later? Compute later.
        result["__features__"] = features  # type: ignore

    return result
