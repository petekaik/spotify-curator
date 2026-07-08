"""Type definitions for the analyzer module.

These dataclasses are the canonical shapes used across the analyzer,
discovery, ranking, and playlist modules. They double as the Parquet
schema for cached profiles.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


@dataclass
class ArtistRef:
    """Lightweight reference to a Spotify artist."""
    id: str
    name: str
    genres: list[str] = field(default_factory=list)
    popularity: Optional[int] = None  # may be None for artists fetched after Feb 2026
    followers: int = 0


@dataclass
class TrackRef:
    """Lightweight reference to a Spotify track."""
    id: str
    name: str
    uri: str
    artist_ids: list[str] = field(default_factory=list)
    artist_names: list[str] = field(default_factory=list)
    album_id: Optional[str] = None
    album_name: Optional[str] = None
    duration_ms: int = 0
    popularity: int = 0


@dataclass
class TimeRangeData:
    """Top-list data for a specific time range."""
    time_range: str          # "short_term" | "medium_term" | "long_term"
    weight: float            # 0.0-1.0, how much to weight this in profile
    artists: list[ArtistRef] = field(default_factory=list)
    tracks: list[TrackRef] = field(default_factory=list)


@dataclass
class TrackFeatures:
    """Audio features centroid (Spotify's audio-features endpoint).

    NOTE: Spotify deprecated audio-features in Nov 2024, but it still works
    as of mid-2026. We'll need a fallback if it goes away.
    """
    tempo: float = 0.0
    energy: float = 0.0
    valence: float = 0.0
    danceability: float = 0.0
    acousticness: float = 0.0
    instrumentalness: float = 0.0
    speechiness: float = 0.0
    liveness: float = 0.0


@dataclass
class UserProfile:
    """User's music taste profile.

    Built from Spotify's top-artists and top-tracks across three time windows,
    weighted by recency. Persisted as Parquet for fast reload.
    """
    user_id: str
    updated_at: datetime
    genre_weights: dict[str, float] = field(default_factory=dict)      # normalized 0-1
    artist_weights: dict[str, float] = field(default_factory=dict)     # artist_id → weight (sums to 1)
    time_ranges: dict[str, TimeRangeData] = field(default_factory=dict)  # "short_term" → TimeRangeData(...)
    saved_album_ids: list[str] = field(default_factory=list)
    saved_track_ids: list[str] = field(default_factory=list)
    features_centroid: Optional[TrackFeatures] = None
    # Metadata about the build
    total_artists: int = 0
    total_tracks: int = 0
    total_genres: int = 0

    def top_genres(self, n: int = 10) -> list[tuple[str, float]]:
        """Return top-N genres by weight."""
        return sorted(
            self.genre_weights.items(),
            key=lambda x: x[1],
            reverse=True,
        )[:n]

    def top_artists(self, n: int = 10) -> list[tuple[str, float]]:
        """Return top-N artists by weight."""
        return sorted(
            self.artist_weights.items(),
            key=lambda x: x[1],
            reverse=True,
        )[:n]

    def to_dict(self) -> dict:
        """Convert to dict for Parquet storage. Datetimes → ISO strings."""
        d = asdict(self)
        d["updated_at"] = self.updated_at.isoformat()
        # Strip non-serializable bits
        if self.features_centroid:
            d["features_centroid"] = asdict(self.features_centroid)
        # Parquet can handle dicts if values are scalars; nested dataclasses need flattening
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "UserProfile":
        """Reconstruct from Parquet dict."""
        updated_at = d["updated_at"]
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at)

        time_ranges: dict[str, TimeRangeData] = {}
        for k, v in d.get("time_ranges", {}).items():
            time_ranges[k] = TimeRangeData(
                time_range=v["time_range"],
                weight=v["weight"],
                artists=[ArtistRef(**a) for a in v.get("artists", [])],
                tracks=[TrackRef(**t) for t in v.get("tracks", [])],
            )

        fc = d.get("features_centroid")
        centroid = TrackFeatures(**fc) if fc else None

        return cls(
            user_id=d["user_id"],
            updated_at=updated_at,
            genre_weights=d.get("genre_weights", {}),
            artist_weights=d.get("artist_weights", {}),
            time_ranges=time_ranges,
            saved_album_ids=d.get("saved_album_ids", []),
            saved_track_ids=d.get("saved_track_ids", []),
            features_centroid=centroid,
            total_artists=d.get("total_artists", 0),
            total_tracks=d.get("total_tracks", 0),
            total_genres=d.get("total_genres", 0),
        )
