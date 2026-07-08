"""Unit tests for analyzer (FP-2)."""
import pytest
from unittest.mock import MagicMock
from datetime import datetime

from src.analyzer.types import ArtistRef, TrackRef, TimeRangeData, UserProfile, TrackFeatures
from src.analyzer.weights import (
    _position_weight,
    compute_artist_weights,
    compute_genre_weights,
    compute_features_centroid,
)
from src.analyzer.cache import save_profile, load_profile, profile_exists
from src.analyzer.profile import ProfileBuilder


# ────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────

@pytest.fixture
def mock_api():
    """Mock SpotifyAPIv2 with spotipy backend."""
    sp = MagicMock()
    # /me
    sp._get.side_effect = lambda path, **kw: (
        {"id": "user123", "display_name": "Pomo"} if path == "me"
        else None
    )
    api = MagicMock()
    api._sp = sp
    return api, sp


@pytest.fixture
def sample_artists_short():
    """50 fake top artists for short_term."""
    return [
        ArtistRef(
            id=f"artist{i}",
            name=f"Artist {i}",
            genres=["indie rock", "dream pop"] if i < 10 else (["shoegaze"] if i < 20 else []),
            popularity=80 - i,
            followers=10000 - i * 100,
        )
        for i in range(50)
    ]


@pytest.fixture
def sample_artists_medium():
    return [
        ArtistRef(
            id=f"artist{i}",
            name=f"Artist {i}",
            genres=["post-rock"] if i < 15 else [],
            popularity=70 - i,
            followers=5000,
        )
        for i in range(50)
    ]


@pytest.fixture
def sample_tracks_short():
    """50 fake top tracks."""
    return [
        TrackRef(
            id=f"track{i}",
            name=f"Track {i}",
            uri=f"spotify:track:track{i}",
            artist_ids=[f"artist{i}"],
            artist_names=[f"Artist {i}"],
            album_id=f"album{i}",
            album_name=f"Album {i}",
            duration_ms=200000,
            popularity=80 - i,
        )
        for i in range(50)
    ]


@pytest.fixture
def sample_time_ranges(sample_artists_short, sample_artists_medium, sample_tracks_short):
    return {
        "short_term": TimeRangeData(
            time_range="short_term",
            weight=0.5,
            artists=sample_artists_short,
            tracks=sample_tracks_short,
        ),
        "medium_term": TimeRangeData(
            time_range="medium_term",
            weight=0.3,
            artists=sample_artists_medium,
            tracks=[],  # simplified
        ),
        "long_term": TimeRangeData(
            time_range="long_term",
            weight=0.2,
            artists=[],
            tracks=[],
        ),
    }


# ────────────────────────────────────────────────────────────
# Types
# ────────────────────────────────────────────────────────────

class TestTypes:
    def test_userprofile_roundtrip(self):
        """Profile → dict → Profile preserves data."""
        p = UserProfile(
            user_id="u1",
            updated_at=datetime(2026, 7, 8),
            genre_weights={"indie": 0.5, "rock": 0.3},
            artist_weights={"a1": 0.7, "a2": 0.3},
        )
        d = p.to_dict()
        assert d["user_id"] == "u1"
        assert d["updated_at"] == "2026-07-08T00:00:00"
        assert d["genre_weights"] == {"indie": 0.5, "rock": 0.3}

        p2 = UserProfile.from_dict(d)
        assert p2.user_id == "u1"
        assert p2.updated_at == datetime(2026, 7, 8)
        assert p2.genre_weights == p.genre_weights

    def test_top_genres(self):
        p = UserProfile(
            user_id="u",
            updated_at=datetime.now(),
            genre_weights={"indie": 0.5, "rock": 0.3, "pop": 0.1, "jazz": 0.1},
            artist_weights={},
        )
        top = p.top_genres(2)
        assert top == [("indie", 0.5), ("rock", 0.3)]


# ────────────────────────────────────────────────────────────
# Weights
# ────────────────────────────────────────────────────────────

class TestWeights:
    def test_position_weight_top(self):
        assert _position_weight(0, 50) == 1.0

    def test_position_weight_bottom(self):
        assert abs(_position_weight(49, 50) - 0.1) < 0.01

    def test_position_weight_single(self):
        assert _position_weight(0, 1) == 1.0

    def test_compute_artist_weights_normalizes(self, sample_time_ranges):
        weights = compute_artist_weights(sample_time_ranges)
        assert sum(weights.values()) == pytest.approx(1.0, abs=0.01)
        # artist0 appears in both short and medium → highest weight
        assert weights["artist0"] > weights["artist40"]

    def test_compute_genre_weights_normalizes(self, sample_time_ranges):
        artist_w = compute_artist_weights(sample_time_ranges)
        genre_w = compute_genre_weights(sample_time_ranges, artist_w)
        assert sum(genre_w.values()) == pytest.approx(1.0, abs=0.01)
        # indie rock and dream pop should be present (top 10 artists)
        assert "indie rock" in genre_w or "dream pop" in genre_w

    def test_features_centroid_with_data(self, sample_time_ranges):
        # Build fake features for some tracks
        features = {
            f"track{i}": {
                "tempo": 120.0,
                "energy": 0.7,
                "valence": 0.5,
                "danceability": 0.4,
                "acousticness": 0.3,
                "instrumentalness": 0.1,
                "speechiness": 0.05,
                "liveness": 0.2,
            }
            for i in range(50)
        }
        artist_w = compute_artist_weights(sample_time_ranges)
        centroid = compute_features_centroid(features, sample_time_ranges, artist_w)
        assert centroid is not None
        # Tempo should de-normalize back to ~120
        assert 100 < centroid.tempo < 140
        assert 0.5 < centroid.energy < 0.9

    def test_features_centroid_empty(self, sample_time_ranges):
        centroid = compute_features_centroid({}, sample_time_ranges, {})
        assert centroid is None


# ────────────────────────────────────────────────────────────
# Cache (Parquet roundtrip)
# ────────────────────────────────────────────────────────────

class TestCache:
    def test_save_and_load_roundtrip(self, tmp_path):
        path = tmp_path / "profile.parquet"
        p = UserProfile(
            user_id="u1",
            updated_at=datetime(2026, 7, 8, 12, 0, 0),
            genre_weights={"indie": 0.5, "rock": 0.3, "ambient": 0.2},
            artist_weights={"a1": 0.4, "a2": 0.3, "a3": 0.2, "a4": 0.1},
            time_ranges={
                "short_term": TimeRangeData(
                    time_range="short_term",
                    weight=0.5,
                    artists=[ArtistRef(id="a1", name="A1", genres=["indie"])],
                    tracks=[TrackRef(id="t1", name="T1", uri="spotify:track:t1")],
                ),
            },
            saved_album_ids=["album1", "album2"],
            features_centroid=TrackFeatures(tempo=120.0, energy=0.7, valence=0.5),
        )
        save_profile(p, path)
        assert profile_exists(path)

        loaded = load_profile(path)
        assert loaded is not None
        assert loaded.user_id == "u1"
        assert loaded.genre_weights == p.genre_weights
        assert loaded.artist_weights == p.artist_weights
        assert loaded.saved_album_ids == ["album1", "album2"]
        assert loaded.features_centroid.tempo == pytest.approx(120.0)
        assert loaded.time_ranges["short_term"].artists[0].id == "a1"

    def test_load_nonexistent(self, tmp_path):
        path = tmp_path / "missing.parquet"
        assert load_profile(path) is None
        assert profile_exists(path) is False


# ────────────────────────────────────────────────────────────
# Profile builder
# ────────────────────────────────────────────────────────────

class TestProfileBuilder:
    def test_user_id_fetched(self, mock_api):
        api, sp = mock_api
        builder = ProfileBuilder(api, user_id="explicit_user")
        assert builder._user_id == "explicit_user"

    def test_user_id_auto_fetch(self, mock_api):
        api, sp = mock_api
        builder = ProfileBuilder(api)
        assert builder._user_id == "user123"

    def test_build_calls_all_endpoints(self, mock_api, sample_artists_short, sample_tracks_short, sample_artists_medium):
        api, sp = mock_api

        # Mock responses for each call
        def mock_get(path, **kw):
            if path == "me":
                return {"id": "user123"}
            if "time_range" in kw:
                if path == "me/top/artists":
                    tr = kw["time_range"]
                    if tr == "short_term":
                        return {"items": [a.__dict__ for a in sample_artists_short]}
                    if tr == "medium_term":
                        return {"items": [a.__dict__ for a in sample_artists_medium]}
                    return {"items": []}
                if path == "me/top/tracks":
                    tr = kw["time_range"]
                    if tr == "short_term":
                        return {"items": [t.__dict__ for t in sample_tracks_short]}
                    return {"items": []}
            if path == "me/library":
                return {"items": []}
            if path == "audio-features":
                return {"audio_features": []}
            return {}

        sp._get.side_effect = mock_get

        builder = ProfileBuilder(api)
        profile = builder.build(include_features=False)

        assert profile.user_id == "user123"
        assert profile.total_artists == 100  # 50 short + 50 medium
        assert profile.total_genres > 0
        assert sum(profile.artist_weights.values()) == pytest.approx(1.0, abs=0.01)
        assert "indie rock" in profile.genre_weights or "dream pop" in profile.genre_weights

    def test_save_and_load_via_builder(self, mock_api, sample_artists_short, sample_tracks_short, tmp_path):
        api, sp = mock_api

        def mock_get(path, **kw):
            if path == "me":
                return {"id": "u1"}
            if path == "me/top/artists":
                return {"items": [a.__dict__ for a in sample_artists_short]}
            if path == "me/top/tracks":
                return {"items": [t.__dict__ for t in sample_tracks_short]}
            if path == "me/library":
                return {"items": []}
            if path == "audio-features":
                return {"audio_features": []}
            return {}

        sp._get.side_effect = mock_get

        path = tmp_path / "p.parquet"
        builder = ProfileBuilder(api)
        profile = builder.build_and_save(include_features=False)
        # Override the default path
        builder.save(profile, path)

        assert path.exists()
        loaded = ProfileBuilder.load_cached(path)
        assert loaded is not None
        assert loaded.user_id == "u1"

    def test_uses_new_library_endpoint(self, mock_api, sample_artists_short, sample_tracks_short):
        """Regression: verify we hit /me/library (NEW), not /me/albums (OLD)."""
        api, sp = mock_api

        def mock_get(path, **kw):
            if path == "me":
                return {"id": "u"}
            if path == "me/top/artists":
                return {"items": [a.__dict__ for a in sample_artists_short]}
            if path == "me/top/tracks":
                return {"items": [t.__dict__ for t in sample_tracks_short]}
            if path == "me/library":
                return {"items": []}
            if path == "audio-features":
                return {"audio_features": []}
            return {}

        sp._get.side_effect = mock_get

        builder = ProfileBuilder(api)
        builder.build(include_features=False)

        # Verify /me/library was called with type=album
        library_calls = [
            c for c in sp._get.call_args_list
            if c[0][0] == "me/library"
        ]
        assert len(library_calls) >= 1
        assert library_calls[0][1].get("type") == "album"
