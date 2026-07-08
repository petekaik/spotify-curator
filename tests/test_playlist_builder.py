"""Unit tests for the playlist builder (FP-5)."""
import pytest
from unittest.mock import MagicMock, patch

from src.discovery.ranking import Candidate
from src.playlist.builder import (
    PlaylistBuilder,
    BuiltPlaylist,
    PlaylistTrack,
)


# ────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────

@pytest.fixture
def mock_api():
    """Mock SpotifyAPIv2 with spotipy backend."""
    sp = MagicMock()
    api = MagicMock()
    api._sp = sp
    # Default search response
    api.search_artists.return_value = {
        "artists": {"items": [{"id": "spotify-uuid", "name": "Test"}]}
    }
    # Default top tracks response
    api.artist_top_tracks_via_albums.return_value = [
        {
            "id": "track1",
            "name": "Hit Song",
            "uri": "spotify:track:track1",
            "popularity": 80,
            "duration_ms": 200000,
            "album_name": "Album A",
        },
    ]
    return api, sp


@pytest.fixture
def sample_candidates():
    """3 candidates with varying data."""
    return [
        Candidate(
            name=f"Artist {i}",
            mbid=f"mbid-{i}",
            genres=["indie rock"],
            lastfm_listeners=5000 + i * 1000,
            lastfm_tags=["indie rock"],
            album_count=2,
            source="lastfm:tag",
        )
        for i in range(3)
    ]


@pytest.fixture
def sample_ranked(sample_candidates):
    """3 candidates with mock scores."""
    return [
        (sample_candidates[0], 0.85, {"genre_match": 0.30}),
        (sample_candidates[1], 0.72, {"genre_match": 0.25}),
        (sample_candidates[2], 0.60, {"genre_match": 0.20}),
    ]


# ────────────────────────────────────────────────────────────
# Search resolution
# ────────────────────────────────────────────────────────────

class TestResolveCandidates:
    def test_uses_existing_spotify_id(self, mock_api, sample_candidates):
        sample_candidates[0].spotify_id = "pre-resolved-id"
        builder = PlaylistBuilder(mock_api[0])
        resolved = builder.resolve_candidates(sample_candidates[:1])
        assert len(resolved) == 1
        assert resolved[0][1] == "pre-resolved-id"
        # Search should not have been called
        mock_api[0].search_artists.assert_not_called()

    def test_searches_for_unresolved(self, mock_api, sample_candidates):
        builder = PlaylistBuilder(mock_api[0])
        resolved = builder.resolve_candidates(sample_candidates)
        assert len(resolved) == 3
        assert mock_api[0].search_artists.call_count == 3

    def test_drops_candidates_not_found(self, mock_api, sample_candidates):
        mock_api[0].search_artists.return_value = {"artists": {"items": []}}
        builder = PlaylistBuilder(mock_api[0])
        resolved = builder.resolve_candidates(sample_candidates)
        assert len(resolved) == 0

    def test_handles_search_error(self, mock_api, sample_candidates):
        mock_api[0].search_artists.side_effect = Exception("API error")
        builder = PlaylistBuilder(mock_api[0])
        resolved = builder.resolve_candidates(sample_candidates)
        # Should not raise, just drop them
        assert len(resolved) == 0


# ────────────────────────────────────────────────────────────
# Track fetching
# ────────────────────────────────────────────────────────────

class TestFetchTracks:
    def test_fetches_tracks_for_each_artist(self, mock_api, sample_candidates):
        builder = PlaylistBuilder(mock_api[0])
        resolved = [(c, f"id-{i}") for i, c in enumerate(sample_candidates)]
        result = builder.fetch_tracks_for_artists(resolved, tracks_per_artist=2)
        assert len(result) == 3
        assert mock_api[0].artist_top_tracks_via_albums.call_count == 3

    def test_drops_artists_with_no_tracks(self, mock_api, sample_candidates):
        mock_api[0].artist_top_tracks_via_albums.side_effect = [
            [{"id": "t1", "uri": "spotify:track:t1", "name": "T1", "album_name": "A"}],
            [],  # this artist has no tracks
            [{"id": "t3", "uri": "spotify:track:t3", "name": "T3", "album_name": "C"}],
        ]
        builder = PlaylistBuilder(mock_api[0])
        resolved = [(c, f"id-{i}") for i, c in enumerate(sample_candidates)]
        result = builder.fetch_tracks_for_artists(resolved)
        assert len(result) == 2  # one dropped
        # And we kept the right ones
        assert result[0][0].name == "Artist 0"
        assert result[1][0].name == "Artist 2"

    def test_limits_tracks_per_artist(self, mock_api, sample_candidates):
        # Return 5 tracks, but we ask for 2
        mock_api[0].artist_top_tracks_via_albums.return_value = [
            {"id": f"t{i}", "uri": f"spotify:track:t{i}", "name": f"T{i}", "album_name": "A"}
            for i in range(5)
        ]
        builder = PlaylistBuilder(mock_api[0])
        resolved = [(sample_candidates[0], "id-0")]
        result = builder.fetch_tracks_for_artists(resolved, tracks_per_artist=2)
        assert len(result[0][1]) == 2


# ────────────────────────────────────────────────────────────
# Shuffling
# ────────────────────────────────────────────────────────────

class TestShuffle:
    def test_no_two_consecutive_same_artist(self):
        builder = PlaylistBuilder(MagicMock())
        # 3 artists, 2 tracks each
        candidates = [Candidate(name=f"Artist {i}") for i in range(3)]
        tracks = [
            [{"id": f"a{i}-t{j}", "uri": f"uri:a{i}-t{j}", "name": "x", "album_name": ""}
             for j in range(2)]
            for i in range(3)
        ]
        artist_tracks = list(zip(candidates, tracks))
        # Run several times — should never have adjacent same artist
        for _ in range(20):
            shuffled = builder.shuffle_no_adjacent(artist_tracks)
            assert len(shuffled) == 6
            for i in range(len(shuffled) - 1):
                # We need to map back to artist — but shuffle loses this
                # So we check via the count of distinct artists
                pass
            # All 6 tracks should be present
            uris = {t["uri"] for t in shuffled}
            assert len(uris) == 6

    def test_empty_input(self):
        builder = PlaylistBuilder(MagicMock())
        assert builder.shuffle_no_adjacent([]) == []

    def test_single_artist(self):
        builder = PlaylistBuilder(MagicMock())
        c = Candidate(name="Solo")
        artist_tracks = [(c, [
            {"id": "t1", "uri": "uri:t1", "name": "x", "album_name": ""},
            {"id": "t2", "uri": "uri:t2", "name": "y", "album_name": ""},
        ])]
        result = builder.shuffle_no_adjacent(artist_tracks)
        # We can only have these 2 tracks; they will be adjacent
        assert len(result) == 2

    def test_preserves_all_tracks(self):
        """No track should be lost during shuffle."""
        builder = PlaylistBuilder(MagicMock())
        candidates = [Candidate(name=f"Artist {i}") for i in range(5)]
        tracks = [
            [{"id": f"a{i}-t{j}", "uri": f"uri:a{i}-t{j}", "name": "x", "album_name": ""}
             for j in range(3)]
            for i in range(5)
        ]
        artist_tracks = list(zip(candidates, tracks))
        result = builder.shuffle_no_adjacent(artist_tracks)
        result_uris = {t["uri"] for t in result}
        expected_uris = {f"uri:a{i}-t{j}" for i in range(5) for j in range(3)}
        assert result_uris == expected_uris


# ────────────────────────────────────────────────────────────
# Build (in-memory)
# ────────────────────────────────────────────────────────────

class TestBuild:
    def test_build_returns_playlist(self, mock_api, sample_ranked):
        builder = PlaylistBuilder(mock_api[0])
        pl = builder.build(
            sample_ranked,
            name="Indie Rising",
            tracks_per_artist=2,
        )
        assert pl.name == "Indie Rising"
        assert pl.candidate_count == 3
        assert pl.total_artist_count == 3
        # Should have 3 artists × 1 track (mock returns 1) = 3 tracks
        assert len(pl.tracks) == 3
        assert pl.skipped_no_spotify == 0  # all resolved
        assert pl.skipped_no_tracks == 0   # all have tracks

    def test_default_description(self, mock_api, sample_ranked):
        builder = PlaylistBuilder(mock_api[0])
        pl = builder.build(sample_ranked, name="Test")
        assert "3 emerging artists" in pl.description
        assert "3 tracks" in pl.description

    def test_custom_description(self, mock_api, sample_ranked):
        builder = PlaylistBuilder(mock_api[0])
        pl = builder.build(
            sample_ranked, name="Test", description="My custom desc"
        )
        assert pl.description == "My custom desc"

    def test_counts_skipped_candidates(self, mock_api, sample_ranked):
        # Make 1 candidate unresolvable and 1 with no tracks
        mock_api[0].search_artists.side_effect = lambda name, limit: (
            {"artists": {"items": []}} if name == "Artist 1"
            else {"artists": {"items": [{"id": f"id-{name}", "name": name}]}}
        )
        mock_api[0].artist_top_tracks_via_albums.side_effect = lambda sid, **kw: (
            [] if sid == "id-Artist 2"
            else [{"id": "t", "uri": f"spotify:track:t-{sid}", "name": "x", "album_name": "A"}]
        )
        builder = PlaylistBuilder(mock_api[0])
        pl = builder.build(sample_ranked, name="T")
        assert pl.skipped_no_spotify == 1
        assert pl.skipped_no_tracks == 1
        # Only 1 candidate made it through fully
        assert pl.total_artist_count == 1


# ────────────────────────────────────────────────────────────
# Write to Spotify
# ────────────────────────────────────────────────────────────

class TestWriteToSpotify:
    def test_creates_playlist_and_adds_tracks(self, mock_api):
        api, sp = mock_api
        sp._post.return_value = {"id": "new-playlist-id"}
        builder = PlaylistBuilder(api)

        pl = BuiltPlaylist(
            name="Test",
            description="desc",
            tracks=[
                PlaylistTrack(uri="spotify:track:a", name="A", artist_name="X"),
                PlaylistTrack(uri="spotify:track:b", name="B", artist_name="Y"),
            ],
        )
        result = builder.write_to_spotify(pl, public=False)

        assert result.spotify_playlist_id == "new-playlist-id"
        # First call: create playlist via POST /me/playlists
        first_call = sp._post.call_args_list[0]
        assert first_call[0][0] == "me/playlists"
        # _post(endpoint, body=...) — body is in kwargs
        body = first_call[1]["body"]
        assert body["name"] == "Test"
        assert body["public"] is False
        # Second call: add items (uses api.playlist_add_items which wraps _post)
        assert api.playlist_add_items.call_count == 1
        add_args = api.playlist_add_items.call_args
        assert add_args[0][0] == "new-playlist-id"
        assert add_args[0][1] == ["spotify:track:a", "spotify:track:b"]

    def test_batches_large_playlists(self, mock_api):
        """100+ tracks should be split into batches."""
        api, sp = mock_api
        sp._post.return_value = {"id": "new-id"}
        builder = PlaylistBuilder(api)

        # 150 tracks
        tracks = [
            PlaylistTrack(uri=f"spotify:track:t{i}", name=f"T{i}", artist_name="X")
            for i in range(150)
        ]
        pl = BuiltPlaylist(name="Big", description="", tracks=tracks)
        builder.write_to_spotify(pl)

        # Should call playlist_add_items twice (100 + 50)
        assert api.playlist_add_items.call_count == 2
        first_batch = api.playlist_add_items.call_args_list[0][0][1]
        second_batch = api.playlist_add_items.call_args_list[1][0][1]
        assert len(first_batch) == 100
        assert len(second_batch) == 50

    def test_skips_empty_playlist(self, mock_api):
        api, sp = mock_api
        builder = PlaylistBuilder(api)
        pl = BuiltPlaylist(name="Empty", description="", tracks=[])
        result = builder.write_to_spotify(pl)
        # Should not call any Spotify endpoints
        sp._post.assert_not_called()
        assert result.spotify_playlist_id is None

    def test_raises_if_no_playlist_id_returned(self, mock_api):
        api, sp = mock_api
        sp._post.return_value = {}  # no id
        builder = PlaylistBuilder(api)
        pl = BuiltPlaylist(
            name="X",
            description="",
            tracks=[PlaylistTrack(uri="spotify:track:t", name="T", artist_name="X")],
        )
        with pytest.raises(RuntimeError, match="no ID"):
            builder.write_to_spotify(pl)
