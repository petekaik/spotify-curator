"""Unit tests for the ranking algorithm (FP-4)."""
import pytest
from datetime import datetime

from src.analyzer.types import UserProfile, TrackFeatures
from src.discovery.sources.lastfm import LastfmArtist
from src.discovery.ranking import (
    Candidate,
    TuningConfig,
    genre_match_score,
    emerging_signal_score,
    feature_match_score,
    discovery_bonus_score,
    mainstream_penalty,
    geo_bonus_score,
    score_artist,
    rank_artists,
    deduplicate_candidates,
)


# ────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────

@pytest.fixture
def profile():
    """User profile that loves indie rock and dream pop."""
    return UserProfile(
        user_id="u1",
        updated_at=datetime(2026, 7, 8),
        genre_weights={
            "indie rock": 0.30,
            "dream pop": 0.20,
            "shoegaze": 0.15,
            "post-rock": 0.10,
            "ambient": 0.05,
            "noise pop": 0.05,
            # rest sums to ~0.15 across less-weighted genres
        },
        artist_weights={"phoebe bridgers": 0.5, "big thief": 0.3, "radiohead": 0.2},
        features_centroid=TrackFeatures(
            tempo=110.0, energy=0.6, valence=0.5,
            danceability=0.4, acousticness=0.6, instrumentalness=0.2,
            speechiness=0.05, liveness=0.2,
        ),
    )


@pytest.fixture
def candidate_matching_genres():
    """Artist that perfectly matches the user's genres."""
    return Candidate(
        name="New Indie Artist",
        mbid="abc",
        genres=["indie rock", "dream pop"],
        lastfm_listeners=5_000,
        lastfm_playcount=50_000,
        album_count=2,
        source="lastfm:tag",
    )


@pytest.fixture
def candidate_wrong_genres():
    """Artist that plays a totally different genre."""
    return Candidate(
        name="Country Singer",
        mbid="def",
        genres=["country", "outlaw country"],
        lastfm_listeners=2_000,
        lastfm_playcount=20_000,
        album_count=5,
        source="lastfm:tag",
    )


@pytest.fixture
def candidate_mainstream():
    """Very popular artist — should get mainstream penalty."""
    return Candidate(
        name="Taylor Swift",
        mbid="ghi",
        genres=["pop", "country pop"],
        lastfm_listeners=5_000_000,
        lastfm_playcount=500_000_000,
        album_count=11,
        source="lastfm:tag",
    )


# ────────────────────────────────────────────────────────────
# Component tests
# ────────────────────────────────────────────────────────────

class TestGenreMatch:
    def test_perfect_genre_overlap(self, profile):
        score = genre_match_score(["indie rock"], profile.genre_weights)
        # 0.30 * 2.5 squash = 0.75
        assert score == pytest.approx(0.75, abs=0.01)

    def test_no_genre_overlap(self, profile):
        score = genre_match_score(["k-pop"], profile.genre_weights)
        assert score == 0.0

    def test_multiple_genres(self, profile):
        score = genre_match_score(
            ["indie rock", "dream pop", "shoegaze"],
            profile.genre_weights,
        )
        # (0.30 + 0.20 + 0.15) * 2.5 = 1.625 → capped at 1.0
        assert score == 1.0

    def test_empty_candidate_genres(self, profile):
        assert genre_match_score([], profile.genre_weights) == 0.0

    def test_empty_profile_genres(self):
        assert genre_match_score(["indie"], {}) == 0.0


class TestEmergingSignal:
    def test_no_listeners_neutral(self):
        c = Candidate(name="X", lastfm_listeners=0)
        assert emerging_signal_score(c) == 0.5

    def test_low_listeners_high_score(self):
        c = Candidate(name="X", lastfm_listeners=1_000)
        # log10(1000) = 3, 3/6 = 0.5, 1 - 0.5 = 0.5
        assert 0.4 < emerging_signal_score(c) < 0.6

    def test_very_low_listeners(self):
        c = Candidate(name="X", lastfm_listeners=10)
        # log10(10) = 1, 1/6 = 0.167, 1 - 0.167 = 0.833
        score = emerging_signal_score(c)
        assert score > 0.8

    def test_high_listeners_low_score(self):
        c = Candidate(name="X", lastfm_listeners=1_000_000)
        # log10(1M) = 6, 6/6 = 1, 1 - 1 = 0
        assert emerging_signal_score(c) == 0.0

    def test_listenbrainz_blended(self):
        c = Candidate(name="X", lastfm_listeners=1_000, listenbrainz_similarity=0.9)
        # 0.7 * 0.5 + 0.3 * 0.9 = 0.62
        assert 0.55 < emerging_signal_score(c) < 0.7


class TestFeatureMatch:
    def test_no_features_neutral(self, profile):
        assert feature_match_score(None, profile.features_centroid) == 0.5
        assert feature_match_score(TrackFeatures(), None) == 0.5

    def test_identical_features(self, profile):
        score = feature_match_score(profile.features_centroid, profile.features_centroid)
        assert score == pytest.approx(1.0, abs=0.01)

    def test_opposite_features(self, profile):
        """All features at extremes should give a low (but not 0) match score."""
        # Profile centroid: tempo=110, energy=0.6 etc.
        # Opposite: max tempo, min everything else
        opposite = TrackFeatures(
            tempo=250.0, energy=0.0, valence=0.0,
            danceability=0.0, acousticness=0.0, instrumentalness=0.0,
            speechiness=0.0, liveness=0.0,
        )
        score = feature_match_score(opposite, profile.features_centroid)
        # Should be noticeably less than the centroid match (1.0)
        # but not necessarily 0 (features aren't all independent)
        assert score < 0.7, f"opposite features should give low score, got {score}"
        # And clearly worse than a close match
        close = TrackFeatures(
            tempo=120.0, energy=0.55, valence=0.45,
            danceability=0.35, acousticness=0.55, instrumentalness=0.25,
            speechiness=0.04, liveness=0.25,
        )
        close_score = feature_match_score(close, profile.features_centroid)
        assert score < close_score


class TestDiscoveryBonus:
    def test_one_album_full_bonus(self):
        c = Candidate(name="X", album_count=1)
        assert discovery_bonus_score(c, TuningConfig()) == 1.0

    def test_many_albums_no_bonus(self):
        c = Candidate(name="X", album_count=20)
        assert discovery_bonus_score(c, TuningConfig()) == 0.0

    def test_interpolation(self):
        c = Candidate(name="X", album_count=5)
        # 1-10 range: 5 is halfway
        score = discovery_bonus_score(c, TuningConfig())
        assert 0.4 < score < 0.6

    def test_unknown_count(self):
        c = Candidate(name="X", album_count=0)
        assert discovery_bonus_score(c, TuningConfig()) == 0.5


class TestMainstreamPenalty:
    def test_below_threshold_no_penalty(self):
        c = Candidate(name="X", lastfm_listeners=10_000)
        assert mainstream_penalty(c, TuningConfig()) == 0.0

    def test_above_threshold(self):
        c = Candidate(name="X", lastfm_listeners=200_000)
        # 200k / 50k = 4, log10(4) ≈ 0.6, /2 = 0.3
        assert 0.25 < mainstream_penalty(c, TuningConfig()) < 0.35

    def test_very_mainstream(self):
        c = Candidate(name="X", lastfm_listeners=5_000_000)
        # 5M / 50k = 100, log10(100) = 2, /2 = 1.0
        assert mainstream_penalty(c, TuningConfig()) == 1.0


class TestGeoBonus:
    def test_no_preferred_tags(self):
        c = Candidate(name="X", lastfm_tags=["finnish"])
        assert geo_bonus_score(c, []) == 0.0

    def test_match(self):
        c = Candidate(name="X", lastfm_tags=["Finnish", "Helsinki"])
        score = geo_bonus_score(c, ["finnish"])
        assert score == pytest.approx(1/3, abs=0.01)

    def test_multiple_matches_cap(self):
        c = Candidate(name="X", lastfm_tags=["finnish", "helsinki", "nordic", "suomi"])
        score = geo_bonus_score(c, ["finnish", "helsinki", "nordic"])
        # 3 matches, capped at 1.0
        assert score == 1.0

    def test_no_match(self):
        c = Candidate(name="X", lastfm_tags=["rock"])
        assert geo_bonus_score(c, ["finnish"]) == 0.0


# ────────────────────────────────────────────────────────────
# End-to-end scoring
# ────────────────────────────────────────────────────────────

class TestScoreArtist:
    def test_indie_artist_outscores_mainstream(
        self, profile, candidate_matching_genres, candidate_mainstream
    ):
        score_indie, _ = score_artist(candidate_matching_genres, profile)
        score_mainstream, _ = score_artist(candidate_mainstream, profile)
        assert score_indie > score_mainstream

    def test_genre_match_artist_outscores_wrong_genre(
        self, profile, candidate_matching_genres, candidate_wrong_genres
    ):
        score_match, _ = score_artist(candidate_matching_genres, profile)
        score_wrong, _ = score_artist(candidate_wrong_genres, profile)
        assert score_match > score_wrong

    def test_breakdown_has_all_components(
        self, profile, candidate_matching_genres
    ):
        _, components = score_artist(candidate_matching_genres, profile)
        expected = {"genre_match", "emerging_signal", "feature_match",
                    "discovery_bonus", "geo_bonus", "mainstream_penalty"}
        assert set(components.keys()) == expected

    def test_score_is_sum_of_components(
        self, profile, candidate_matching_genres
    ):
        total, components = score_artist(candidate_matching_genres, profile)
        assert total == pytest.approx(sum(components.values()), abs=0.001)

    def test_geo_bonus_increases_score(self, profile, candidate_matching_genres):
        score_no_geo, _ = score_artist(candidate_matching_genres, profile)
        # Add finnish to candidate's tags
        candidate_matching_genres.lastfm_tags.append("finnish")
        score_with_geo, _ = score_artist(
            candidate_matching_genres, profile, preferred_geo_tags=["finnish"]
        )
        assert score_with_geo > score_no_geo


class TestConfigValidation:
    def test_default_config_valid(self):
        TuningConfig().validate()  # should not raise

    def test_invalid_weight_raises(self):
        c = TuningConfig(genre_match=2.0)
        with pytest.raises(ValueError, match="genre_match"):
            c.validate()

    def test_invalid_penalty_raises(self):
        c = TuningConfig(mainstream_penalty=0.5)  # should be ≤ 0
        with pytest.raises(ValueError, match="mainstream_penalty"):
            c.validate()


class TestRankArtists:
    def test_orders_by_score(
        self, profile, candidate_matching_genres, candidate_wrong_genres, candidate_mainstream
    ):
        ranked = rank_artists(
            [candidate_wrong_genres, candidate_mainstream, candidate_matching_genres],
            profile,
        )
        # Matching genres should be first
        assert ranked[0][0].name == "New Indie Artist"
        # Mainstream last
        assert ranked[-1][0].name == "Taylor Swift"

    def test_limit(self, profile, candidate_matching_genres, candidate_wrong_genres):
        ranked = rank_artists(
            [candidate_matching_genres, candidate_wrong_genres],
            profile,
            limit=1,
        )
        assert len(ranked) == 1


class TestDeduplicate:
    def test_dedup_by_name_case_insensitive(self):
        """Multiple entries for same artist: keep the one with most data."""
        c1 = Candidate(name="Phoebe Bridgers", lastfm_listeners=1000)
        c2 = Candidate(name="phoebe bridgers", lastfm_listeners=2000)
        # c3 has the MOST data: spotify_id (2.0) + mbid (1.0) + listeners (1.0) + genres (0.5*N)
        c3 = Candidate(
            name="Phoebe Bridgers",
            mbid="mbid-x",
            spotify_id="spotify-uuid",
            genres=["indie", "sad girl", "indie rock"],
            lastfm_listeners=3000,
        )
        result = deduplicate_candidates([c1, c2, c3])
        assert len(result) == 1
        # The most data-rich candidate wins
        assert result[0].spotify_id == "spotify-uuid"

    def test_dedup_different_names_kept(self):
        c1 = Candidate(name="Artist A")
        c2 = Candidate(name="Artist B")
        result = deduplicate_candidates([c1, c2])
        assert len(result) == 2

    def test_dedup_empty(self):
        assert deduplicate_candidates([]) == []


class TestFromLastfm:
    def test_converts_lastfm_artist(self):
        lfm = LastfmArtist(
            name="Big Thief",
            mbid="mbid-bigthief",
            listeners=800_000,
            playcount=12_000_000,
            tags=["indie rock"],
            similar=["Adrianne Lenker"],
        )
        c = Candidate.from_lastfm(lfm, source="lastfm:tag")
        assert c.name == "Big Thief"
        assert c.mbid == "mbid-bigthief"
        assert c.lastfm_listeners == 800_000
        assert c.lastfm_tags == ["indie rock"]
        assert c.similar_artists == ["Adrianne Lenker"]
        assert c.source == "lastfm:tag"
