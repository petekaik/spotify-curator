"""User profile builder — main entry point for FP-2.

Usage:
    >>> from src.spotify.auth import get_spotify_client
    >>> from src.spotify.api_v2 import SpotifyAPIv2
    >>> from src.analyzer.profile import ProfileBuilder
    >>>
    >>> sp = get_spotify_client()
    >>> api = SpotifyAPIv2(sp)
    >>> builder = ProfileBuilder(api)
    >>> profile = builder.build()
    >>> print(profile.top_genres(5))
    >>> builder.save()
"""
from __future__ import annotations

import logging
from datetime import datetime

from src.spotify.api_v2 import SpotifyAPIv2
from src.analyzer.types import UserProfile
from src.analyzer.fetch import fetch_all_for_profile, fetch_saved_albums
from src.analyzer.weights import (
    compute_artist_weights,
    compute_genre_weights,
    compute_features_centroid,
)
from src.analyzer.cache import save_profile, load_profile, PROFILE_PATH

log = logging.getLogger(__name__)


class ProfileBuilder:
    """Build and persist user taste profiles.

    The builder does NOT cache the spotipy client — caller passes a fresh
    one. This makes it testable with mocks.
    """

    def __init__(self, api: SpotifyAPIv2, user_id: str | None = None):
        """Initialize with a SpotifyAPIv2 instance.

        Args:
            api: authenticated SpotifyAPIv2 wrapper
            user_id: Spotify user ID (auto-fetched if None)
        """
        self._api = api
        self._user_id = user_id or self._fetch_user_id()
        log.info(f"ProfileBuilder initialized for user {self._user_id}")

    def _fetch_user_id(self) -> str:
        """Get current user's Spotify ID."""
        user = self._api._sp._get("me")
        return user["id"]

    def build(self, include_features: bool = True) -> UserProfile:
        """Build a complete UserProfile from current Spotify data.

        Steps:
            1. Fetch top artists + tracks for all 3 time ranges
            2. Optionally fetch audio features for the top tracks
            3. Compute weighted artist scores
            4. Compute weighted genre scores
            5. Compute features centroid
            6. Fetch saved album IDs
            7. Return UserProfile dataclass

        Args:
            include_features: whether to fetch audio features (deprecated endpoint,
                              may fail gracefully)

        Returns:
            UserProfile
        """
        log.info("Building user profile...")
        time_ranges = fetch_all_for_profile(self._api, include_features=include_features)
        features_by_track_raw = time_ranges.pop("__features__", {})
        # Defensive: ensure it's a dict
        features_by_track: dict[str, dict] = (
            features_by_track_raw if isinstance(features_by_track_raw, dict) else {}
        )

        artist_weights = compute_artist_weights(time_ranges)
        genre_weights = compute_genre_weights(time_ranges, artist_weights)
        centroid = compute_features_centroid(features_by_track, time_ranges, artist_weights)

        log.info("Fetching saved albums...")
        saved_album_ids = fetch_saved_albums(self._api, limit=50)

        # Compute totals
        total_artists = sum(len(td.artists) for td in time_ranges.values())
        total_tracks = sum(len(td.tracks) for td in time_ranges.values())
        total_genres = len(genre_weights)

        profile = UserProfile(
            user_id=self._user_id,
            updated_at=datetime.now(),
            genre_weights=genre_weights,
            artist_weights=artist_weights,
            time_ranges=time_ranges,
            saved_album_ids=saved_album_ids,
            features_centroid=centroid,
            total_artists=total_artists,
            total_tracks=total_tracks,
            total_genres=total_genres,
        )
        log.info(
            f"Profile built: {total_artists} artists, "
            f"{total_genres} genres, {total_tracks} tracks"
        )
        return profile

    def save(self, profile: UserProfile, path=PROFILE_PATH) -> None:
        """Persist the profile to Parquet cache."""
        save_profile(profile, path)

    def build_and_save(self, include_features: bool = True) -> UserProfile:
        """Convenience: build + save in one call."""
        profile = self.build(include_features=include_features)
        self.save(profile)
        return profile

    @staticmethod
    def load_cached(path=PROFILE_PATH) -> UserProfile | None:
        """Load a previously cached profile, or None if no cache."""
        return load_profile(path)
