"""Artist ranking for the "emerging + taste match" problem.

Given:
- A user profile (FP-2): genre_weights, artist_weights
- A list of candidate artists from any discovery source (FP-3)
- Audio features for some candidates (when available)

Compute a score that ranks artists by:
1. How well they match the user's taste (genre, audio features)
2. How "emerging" they are (low listeners, recent activity, growth)
3. **Not** too mainstream (penalty for high follower count)

Score formula:
    artist_score = (
        0.40 * genre_match       # cosine similarity of genre vectors
      + 0.25 * emerging_signal   # cross-source emergence (low listeners, recency)
      + 0.20 * feature_match     # audio features centroid distance
      + 0.10 * discovery_bonus   # fewer albums = newer artist
      + 0.05 * geo_bonus         # local scene (configurable)
      - 0.30 * mainstream_penalty  # > N listeners = too big
    )

All weights are configurable via TuningConfig.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from src.analyzer.types import UserProfile, TrackFeatures
from src.discovery.sources.lastfm import LastfmArtist


# ────────────────────────────────────────────────────────────
# Configuration
# ────────────────────────────────────────────────────────────

@dataclass
class TuningConfig:
    """Tunable weights for the ranking formula.

    Defaults are designed to err toward "more obscure" — that's the
    whole point of this project. If you want more popular results,
    lower mainstream_penalty and raise emerging_signal.

    Validation: weights need not sum to 1.0 (we use multipliers),
    but the relative magnitudes matter. mainstream_penalty is negative
    (subtracted from score).
    """
    genre_match: float = 0.40
    emerging_signal: float = 0.25
    feature_match: float = 0.20
    discovery_bonus: float = 0.10
    geo_bonus: float = 0.05
    mainstream_penalty: float = -0.30

    # Mainstream threshold: artists with listeners > this get penalized
    # Last.fm listeners is a different scale than Spotify followers, so
    # we use Last.fm numbers here. 50k is a reasonable "still emerging" cap.
    mainstream_listener_threshold: int = 50_000

    # Penalty curve: how aggressively to penalize above threshold
    # 0 = binary, 1 = linear
    penalty_steepness: float = 1.0

    # Discovery bonus: how much to favor artists with fewer albums
    # A score of 0.10 means artists with 1 album get full bonus,
    # 10+ albums get zero bonus
    discovery_album_full_bonus: int = 1
    discovery_album_zero_bonus: int = 10

    def validate(self) -> None:
        """Sanity check: ensure weights are in plausible range."""
        for name in ("genre_match", "emerging_signal", "feature_match",
                     "discovery_bonus", "geo_bonus"):
            v = getattr(self, name)
            if not (0.0 <= v <= 1.0):
                raise ValueError(f"{name} must be in [0, 1], got {v}")
        if not (-1.0 <= self.mainstream_penalty <= 0.0):
            raise ValueError(f"mainstream_penalty must be in [-1, 0], got {self.mainstream_penalty}")


# ────────────────────────────────────────────────────────────
# Candidate type (unifies Last.fm, ListenBrainz, future sources)
# ────────────────────────────────────────────────────────────

@dataclass
class Candidate:
    """An artist candidate from any discovery source.

    Used to feed into the ranking algorithm. The fields are optional
    because not all sources provide all data.
    """
    name: str
    mbid: Optional[str] = None
    spotify_id: Optional[str] = None  # if resolved via MusicBrainz→Spotify

    # Genre data (from MusicBrainz or Spotify artist.genres)
    genres: list[str] = field(default_factory=list)

    # Last.fm metrics
    lastfm_listeners: int = 0
    lastfm_playcount: int = 0
    lastfm_tags: list[str] = field(default_factory=list)
    similar_artists: list[str] = field(default_factory=list)

    # ListenBrainz similarity score (0.0-1.0)
    listenbrainz_similarity: float = 0.0

    # Audio features (Spotify audio-features endpoint, deprecated)
    track_features: Optional[TrackFeatures] = None

    # Discovery metrics
    album_count: int = 0
    first_release_year: Optional[int] = None
    last_release_year: Optional[int] = None

    # Source provenance
    source: str = ""  # "lastfm:tag" | "lastfm:similar" | "listenbrainz" | etc.
    tags_from_user: list[str] = field(default_factory=list)  # for geo_bonus

    @classmethod
    def from_lastfm(cls, lfm: LastfmArtist, source: str = "lastfm:tag") -> "Candidate":
        """Build a Candidate from a LastfmArtist."""
        return cls(
            name=lfm.name,
            mbid=lfm.mbid,
            lastfm_listeners=lfm.listeners,
            lastfm_playcount=lfm.playcount,
            lastfm_tags=lfm.tags,
            similar_artists=lfm.similar,
            source=source,
        )


# ────────────────────────────────────────────────────────────
# Scoring components
# ────────────────────────────────────────────────────────────

def genre_match_score(
    candidate_genres: list[str],
    profile_genre_weights: dict[str, float],
) -> float:
    """Compute genre match between candidate and user profile.

    Two strategies:
    1. Sum of profile weights for matching genres (capped at 1.0)
    2. Cosine similarity (only if both vectors are non-empty)

    We use strategy 1 for interpretability and to handle sparse
    candidate genre data well (most indie artists have 1-3 genres).

    Args:
        candidate_genres: list of genre names from candidate
        profile_genre_weights: dict of genre → weight from profile

    Returns:
        score in [0.0, 1.0]
    """
    if not candidate_genres or not profile_genre_weights:
        return 0.0
    score = sum(profile_genre_weights.get(g, 0.0) for g in candidate_genres)
    # Sigmoid-like squash so a single exact match doesn't max the score
    return min(1.0, score * 2.5)


def emerging_signal_score(candidate: Candidate) -> float:
    """Compute how "emerging" this artist is.

    Two signals combined:
    - Low listener count = small but visible
    - Last.fm tag matches from user's profile = high community engagement

    Note: "growth rate" would require tracking listeners over time,
    which is out of scope for v1. We use a static proxy.

    Returns:
        score in [0.0, 1.0]
    """
    # Inverse log of listener count: 0 listeners = 1.0, 1M listeners = 0.0
    if candidate.lastfm_listeners <= 0:
        listener_score = 0.5  # unknown, neutral
    else:
        # log10(1) = 0, log10(1M) = 6
        listener_score = max(0.0, 1.0 - (math.log10(candidate.lastfm_listeners) / 6.0))

    # ListenBrainz similarity: 0-1 directly
    lb_score = max(0.0, min(1.0, candidate.listenbrainz_similarity))

    # Weighted: 70% listener scarcity, 30% ML signal (if available)
    if candidate.listenbrainz_similarity > 0:
        return 0.7 * listener_score + 0.3 * lb_score
    return listener_score


def feature_match_score(
    candidate_features: Optional[TrackFeatures],
    profile_centroid: Optional[TrackFeatures],
) -> float:
    """Compute audio features match between candidate and user centroid.

    Returns 0.5 (neutral) if either side has no features.

    Args:
        candidate_features: track features aggregated for this artist's tracks
        profile_centroid: user profile's feature centroid

    Returns:
        score in [0.0, 1.0], where 1.0 = perfect match
    """
    if candidate_features is None or profile_centroid is None:
        return 0.5  # unknown, neutral

    # Compute Euclidean distance on normalized features
    # All features are 0-1 except tempo (0-250 BPM)
    keys = ("energy", "valence", "danceability", "acousticness",
            "instrumentalness", "speechiness", "liveness")
    c_vec = [getattr(candidate_features, k, 0) for k in keys]
    p_vec = [getattr(profile_centroid, k, 0) for k in keys]
    # Normalize tempo
    c_tempo = candidate_features.tempo / 250.0
    p_tempo = profile_centroid.tempo / 250.0

    # Combine into one vector
    c_full = c_vec + [c_tempo]
    p_full = p_vec + [p_tempo]

    # Euclidean distance, max possible is sqrt(8) ≈ 2.83
    dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(c_full, p_full)))
    max_dist = math.sqrt(len(c_full))
    # Convert distance to similarity (1 = identical, 0 = max distance)
    similarity = max(0.0, 1.0 - (dist / max_dist))
    return similarity


def discovery_bonus_score(candidate: Candidate, config: TuningConfig) -> float:
    """Score favoring newer artists (fewer albums).

    Artists with FEWER albums get HIGHER bonus.
    The intuition: artists with 1-2 albums are more likely "emerging" than
    artists with 10+ albums (which suggests they're established).

    Args:
        candidate: the candidate
        config: tuning config

    Returns:
        score in [0.0, 1.0]
    """
    if candidate.album_count <= 0:
        return 0.5  # unknown, neutral
    if candidate.album_count <= config.discovery_album_full_bonus:
        return 1.0
    if candidate.album_count >= config.discovery_album_zero_bonus:
        return 0.0
    # Linear interpolation
    span = config.discovery_album_zero_bonus - config.discovery_album_full_bonus
    return 1.0 - (candidate.album_count - config.discovery_album_full_bonus) / span


def mainstream_penalty(candidate: Candidate, config: TuningConfig) -> float:
    """Penalty for being too mainstream (high listener count).

    Returns 0.0 if below threshold, increasing up to 1.0 for very large artists.
    The penalty is SUBTRACTED from the final score (so high = bad).

    Args:
        candidate: the candidate
        config: tuning config (mainstream_listener_threshold, penalty_steepness)

    Returns:
        penalty in [0.0, 1.0]
    """
    if candidate.lastfm_listeners <= config.mainstream_listener_threshold:
        return 0.0
    # How far above threshold (log scale)
    over = candidate.lastfm_listeners / config.mainstream_listener_threshold
    # 1.0x threshold = 0 penalty, 100x threshold = ~1.0 penalty
    penalty = min(1.0, math.log10(over) / 2.0)  # log10(100) = 2
    return penalty * config.penalty_steepness


def geo_bonus_score(candidate: Candidate, preferred_tags: list[str]) -> float:
    """Local scene bonus if the artist's tags match user's preferred geo tags.

    Args:
        candidate: the candidate
        preferred_tags: e.g. ["finnish", "helsinki", "nordic"] for Finnish scene

    Returns:
        bonus in [0.0, 1.0]
    """
    if not preferred_tags:
        return 0.0
    candidate_tags_lower = [t.lower() for t in candidate.lastfm_tags + candidate.tags_from_user]
    matches = sum(1 for t in preferred_tags if t.lower() in candidate_tags_lower)
    if matches == 0:
        return 0.0
    return min(1.0, matches / 3.0)  # 3 matches = full bonus


# ────────────────────────────────────────────────────────────
# Main ranking function
# ────────────────────────────────────────────────────────────

def score_artist(
    candidate: Candidate,
    profile: UserProfile,
    config: Optional[TuningConfig] = None,
    preferred_geo_tags: Optional[list[str]] = None,
) -> tuple[float, dict[str, float]]:
    """Score a single artist candidate against the user profile.

    Returns:
        (total_score, component_breakdown)
        - total_score: weighted sum, higher is better
        - component_breakdown: dict mapping component name → its contribution
    """
    if config is None:
        config = TuningConfig()
    config.validate()

    if preferred_geo_tags is None:
        preferred_geo_tags = []

    components: dict[str, float] = {}

    # Combine Last.fm tags and candidate genres
    all_genres = list(set(candidate.genres + candidate.lastfm_tags))
    components["genre_match"] = config.genre_match * genre_match_score(
        all_genres, profile.genre_weights
    )
    components["emerging_signal"] = config.emerging_signal * emerging_signal_score(candidate)
    components["feature_match"] = config.feature_match * feature_match_score(
        candidate.track_features, profile.features_centroid
    )
    components["discovery_bonus"] = config.discovery_bonus * discovery_bonus_score(
        candidate, config
    )
    components["geo_bonus"] = config.geo_bonus * geo_bonus_score(
        candidate, preferred_geo_tags
    )
    # Penalty is negative
    penalty = mainstream_penalty(candidate, config)
    components["mainstream_penalty"] = config.mainstream_penalty * penalty

    total = sum(components.values())
    return total, components


def rank_artists(
    candidates: list[Candidate],
    profile: UserProfile,
    config: Optional[TuningConfig] = None,
    preferred_geo_tags: Optional[list[str]] = None,
    limit: Optional[int] = None,
) -> list[tuple[Candidate, float, dict[str, float]]]:
    """Rank a list of candidates by score.

    Args:
        candidates: list of Candidate objects
        profile: user profile (from FP-2)
        config: optional tuning overrides
        preferred_geo_tags: optional list of geo tags for local scene bonus
        limit: max number of results to return

    Returns:
        list of (candidate, total_score, component_breakdown) sorted by score desc
    """
    scored: list[tuple[Candidate, float, dict[str, float]]] = []
    for c in candidates:
        total, components = score_artist(c, profile, config, preferred_geo_tags)
        scored.append((c, total, components))

    # Sort by total score desc
    scored.sort(key=lambda x: x[1], reverse=True)

    if limit is not None:
        scored = scored[:limit]
    return scored


def deduplicate_candidates(
    candidates: list[Candidate],
    key_fn: Optional[callable] = None,
) -> list[Candidate]:
    """Deduplicate candidates by some key (default: lowercased name).

    Keeps the candidate with the highest data quality (most fields filled).
    """
    if key_fn is None:
        key_fn = lambda c: c.name.lower().strip()

    seen: dict[str, Candidate] = {}
    for c in candidates:
        key = key_fn(c)
        if key not in seen:
            seen[key] = c
            continue
        # If existing candidate has less data, replace it
        existing = seen[key]
        if _data_quality(c) > _data_quality(existing):
            seen[key] = c
    return list(seen.values())


def _data_quality(c: Candidate) -> float:
    """Crude measure of how much data this candidate has.

    Returns a float so candidates can be compared even when they have
    different fields filled in. Higher = more data.
    """
    score = 0.0
    if c.mbid is not None:
        score += 1.0
    if c.spotify_id is not None:
        score += 2.0  # Spotify ID is high-value for downstream lookups
    if c.genres:
        score += 0.5 * len(c.genres)  # more genres = more info
    if c.lastfm_listeners > 0:
        score += 1.0
    if c.lastfm_playcount > 0:
        score += 0.5
    if c.listenbrainz_similarity > 0:
        score += 1.0
    if c.track_features is not None:
        score += 2.0  # audio features are expensive to compute
    if c.album_count > 0:
        score += 0.5
    if c.similar_artists:
        score += 0.3 * len(c.similar_artists)
    return score
