"""Compute weighted user profile from fetched Spotify data.

The weighting scheme blends three time windows:
  - short_term (0.5) — last 4 weeks, most important
  - medium_term (0.3) — last 6 months, stable
  - long_term (0.2) — all time, foundational

Each artist's position in the top-list within a time-range also contributes
to its weight (top of the list = stronger signal than 50th place).
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Optional

from src.analyzer.types import (
    ArtistRef,
    TrackRef,
    TimeRangeData,
    UserProfile,
    TrackFeatures,
)


def _position_weight(rank: int, list_size: int) -> float:
    """Compute weight for an artist's position in a top-list.

    Linear decay: rank 0 = 1.0, rank N-1 = 0.1.
    So #1 in a 50-list gets 1.0, #50 gets 0.1.

    Args:
        rank: 0-based rank in the list
        list_size: total number of items in the list

    Returns:
        weight between 0.1 and 1.0
    """
    if list_size <= 1:
        return 1.0
    # Linear: w = 1.0 - (rank / (list_size - 1)) * 0.9
    return 1.0 - (rank / (list_size - 1)) * 0.9


def compute_artist_weights(
    time_ranges: dict[str, TimeRangeData],
) -> dict[str, float]:
    """Compute blended artist weights across time ranges.

    Algorithm:
        for each artist across all time ranges:
            weight = sum(
                time_range_weight * position_weight
                for each time_range where artist appears
            )

    Args:
        time_ranges: dict mapping time_range → TimeRangeData

    Returns:
        dict mapping artist_id → normalized weight (sums to ~1.0)
    """
    raw: dict[str, float] = defaultdict(float)

    for tr_key, tr_data in time_ranges.items():
        if tr_key == "__features__":
            continue
        n = len(tr_data.artists)
        for rank, artist in enumerate(tr_data.artists):
            pos_w = _position_weight(rank, n)
            contribution = tr_data.weight * pos_w
            raw[artist.id] += contribution

    # Normalize
    total = sum(raw.values())
    if total == 0:
        return {}
    return {k: v / total for k, v in raw.items()}


def compute_genre_weights(
    time_ranges: dict[str, TimeRangeData],
    artist_weights: dict[str, float],
) -> dict[str, float]:
    """Compute genre weights based on the weighted artist set.

    Each genre's weight = sum of its artists' weights.

    Args:
        time_ranges: dict mapping time_range → TimeRangeData
        artist_weights: dict mapping artist_id → weight (output of compute_artist_weights)

    Returns:
        dict mapping genre → normalized weight (sums to ~1.0)
    """
    genre_raw: dict[str, float] = defaultdict(float)

    # For each artist in each time range, look up its weight and add to its genres
    for tr_key, tr_data in time_ranges.items():
        if tr_key == "__features__":
            continue
        for artist in tr_data.artists:
            w = artist_weights.get(artist.id, 0.0)
            for genre in artist.genres:
                genre_raw[genre] += w

    # Normalize
    total = sum(genre_raw.values())
    if total == 0:
        return {}
    return {k: v / total for k, v in genre_raw.items()}


def compute_features_centroid(
    features_by_track: dict[str, dict],
    time_ranges: dict[str, TimeRangeData],
    artist_weights: dict[str, float],
) -> Optional[TrackFeatures]:
    """Compute the audio features centroid, weighted by track's artist.

    For each track: weight = its primary artist's weight.
    Centroid = weighted average of (tempo, energy, valence, etc.)

    Args:
        features_by_track: dict track_id → features dict (from Spotify)
        time_ranges: dict of time-range data (we walk all tracks)
        artist_weights: dict artist_id → weight

    Returns:
        TrackFeatures with averaged values, or None if no features
    """
    if not features_by_track:
        return None

    # Accumulate weighted sums
    sums = defaultdict(float)
    total_weight = 0.0

    for tr_key, tr_data in time_ranges.items():
        if tr_key == "__features__":
            continue
        for track in tr_data.tracks:
            if track.id not in features_by_track:
                continue
            # Weight by the track's primary artist's profile weight
            primary_artist = track.artist_ids[0] if track.artist_ids else None
            w = artist_weights.get(primary_artist, 0.0) if primary_artist else 0.0
            # If we have no artist weight, fall back to position weight
            if w == 0.0:
                w = 0.01
            feat = features_by_track[track.id]
            for key in ("tempo", "energy", "valence", "danceability",
                       "acousticness", "instrumentalness", "speechiness", "liveness"):
                val = feat.get(key)
                if val is None:
                    continue
                # Normalize tempo to 0-1 (Spotify returns 0-250 BPM)
                if key == "tempo":
                    val = val / 250.0
                sums[key] += val * w
            total_weight += w

    if total_weight == 0:
        return None

    return TrackFeatures(
        tempo=sums["tempo"] / total_weight * 250.0,  # de-normalize
        energy=sums["energy"] / total_weight,
        valence=sums["valence"] / total_weight,
        danceability=sums["danceability"] / total_weight,
        acousticness=sums["acousticness"] / total_weight,
        instrumentalness=sums["instrumentalness"] / total_weight,
        speechiness=sums["speechiness"] / total_weight,
        liveness=sums["liveness"] / total_weight,
    )
