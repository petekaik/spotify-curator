"""Parquet-based profile cache.

Profiles are persisted to ~/.spotify-curator/profile.parquet so we don't
have to hit the Spotify API on every CLI run. The Parquet format is:
- Fast columnar read/write
- Schema-safe
- Human-inspectable with pandas
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from src.analyzer.types import UserProfile, TimeRangeData, ArtistRef, TrackRef

log = logging.getLogger(__name__)

CACHE_DIR = Path.home() / ".spotify-curator"
PROFILE_PATH = CACHE_DIR / "profile.parquet"


def _ensure_cache_dir() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def save_profile(profile: UserProfile, path: Path = PROFILE_PATH) -> None:
    """Save a UserProfile to Parquet.

    We flatten the nested structure (artists, tracks, time ranges, etc.)
    into a single-row DataFrame where complex fields are stored as JSON
    strings for portability and schema stability.

    Args:
        profile: UserProfile to save
        path: target path (defaults to ~/.spotify-curator/profile.parquet)
    """
    _ensure_cache_dir()

    d = profile.to_dict()
    # Flatten everything to JSON strings (except user_id and updated_at)
    row = {
        "user_id": d["user_id"],
        "updated_at": d["updated_at"],
        "genre_weights": json.dumps(d["genre_weights"]),
        "artist_weights": json.dumps(d["artist_weights"]),
        "time_ranges": json.dumps(d["time_ranges"]),
        "saved_album_ids": json.dumps(d["saved_album_ids"]),
        "saved_track_ids": json.dumps(d["saved_track_ids"]),
        "features_centroid": json.dumps(d["features_centroid"]) if d.get("features_centroid") else None,
        "total_artists": d["total_artists"],
        "total_tracks": d["total_tracks"],
        "total_genres": d["total_genres"],
    }
    df = pd.DataFrame([row])
    table = pa.Table.from_pandas(df)
    pq.write_table(table, str(path))
    log.info(f"Saved profile to {path}")


def load_profile(path: Path = PROFILE_PATH) -> UserProfile | None:
    """Load a UserProfile from Parquet. Returns None if file doesn't exist."""
    if not path.exists():
        return None
    table = pq.read_table(str(path))
    df = table.to_pandas()
    if df.empty:
        return None
    row = df.iloc[0].to_dict()

    # Parse JSON columns back
    d = {
        "user_id": row["user_id"],
        "updated_at": row["updated_at"],
        "genre_weights": json.loads(row["genre_weights"]) if row["genre_weights"] else {},
        "artist_weights": json.loads(row["artist_weights"]) if row["artist_weights"] else {},
        "time_ranges": json.loads(row["time_ranges"]) if row["time_ranges"] else {},
        "saved_album_ids": json.loads(row["saved_album_ids"]) if row["saved_album_ids"] else [],
        "saved_track_ids": json.loads(row["saved_track_ids"]) if row["saved_track_ids"] else [],
        "features_centroid": json.loads(row["features_centroid"]) if row["features_centroid"] else None,
        "total_artists": int(row["total_artists"]),
        "total_tracks": int(row["total_tracks"]),
        "total_genres": int(row["total_genres"]),
    }
    return UserProfile.from_dict(d)


def profile_exists(path: Path = PROFILE_PATH) -> bool:
    return path.exists()


def profile_age_hours(path: Path = PROFILE_PATH) -> float | None:
    """Return how old the cached profile is in hours, or None if no file."""
    if not path.exists():
        return None
    mtime = path.stat().st_mtime
    age_seconds = (datetime.now().timestamp() - mtime)
    return age_seconds / 3600.0
