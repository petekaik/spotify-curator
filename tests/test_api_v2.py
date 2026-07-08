"""Unit tests for Spotify API v2 wrapper.

These tests do NOT make real API calls. They mock spotipy._get/_post/_put/_delete
to verify our wrapper builds the correct request paths and bodies.
"""
import pytest
from unittest.mock import MagicMock
from src.spotify.api_v2 import SpotifyAPIv2


@pytest.fixture
def mock_sp():
    """Create a mock spotipy.Spotify client."""
    sp = MagicMock()
    sp._get = MagicMock(return_value={"items": []})
    sp._post = MagicMock(return_value={"snapshot_id": "abc123"})
    sp._put = MagicMock(return_value={})
    sp._delete = MagicMock(return_value={})
    return sp


@pytest.fixture
def api(mock_sp):
    """Create APIv2 wrapper with mocked spotipy client."""
    return SpotifyAPIv2(mock_sp)


# ────────────────────────────────────────────────────────────
# Library endpoints
# ────────────────────────────────────────────────────────────

class TestLibrary:
    def test_library_contains_single(self, api, mock_sp):
        api.library_contains("track", ["track123"])
        mock_sp._get.assert_called_once_with(
            "me/library/contains",
            type="track",
            ids="track123",
        )

    def test_library_contains_multiple(self, api, mock_sp):
        mock_sp._get.return_value = [True, False, True]
        result = api.library_contains("album", ["a1", "a2", "a3"])
        assert result == [True, False, True]
        mock_sp._get.assert_called_once_with(
            "me/library/contains",
            type="album",
            ids="a1,a2,a3",
        )

    def test_library_save(self, api, mock_sp):
        api.library_save("track", ["t1", "t2"])
        mock_sp._put.assert_called_once_with(
            "me/library",
            body={"ids": ["t1", "t2"], "type": "track"},
        )

    def test_library_remove(self, api, mock_sp):
        api.library_remove("album", ["a1"])
        mock_sp._delete.assert_called_once_with(
            "me/library",
            body={"ids": ["a1"], "type": "album"},
        )

    def test_library_too_many_raises(self, api):
        with pytest.raises(ValueError, match="max 50"):
            api.library_contains("track", ["t" + str(i) for i in range(51)])


# ────────────────────────────────────────────────────────────
# Playlist endpoints (new paths)
# ────────────────────────────────────────────────────────────

class TestPlaylists:
    def test_playlist_items_uses_new_path(self, api, mock_sp):
        mock_sp._get.return_value = {"items": [], "total": 0}
        api.playlist_items("playlist123", limit=50)
        # CRITICAL: must use /playlists/{id}/items (new), NOT /playlists/{id}/tracks
        mock_sp._get.assert_called_once()
        call_args = mock_sp._get.call_args
        assert call_args[0][0] == "playlists/playlist123/items"
        assert call_args[1]["limit"] == 50  # capped to 100 by wrapper

    def test_playlist_items_caps_limit_at_100(self, api, mock_sp):
        api.playlist_items("p1", limit=200)
        call_args = mock_sp._get.call_args
        assert call_args[1]["limit"] == 100

    def test_playlist_add_items(self, api, mock_sp):
        uris = ["spotify:track:abc", "spotify:track:def"]
        result = api.playlist_add_items("playlist123", uris)
        assert result == {"snapshot_id": "abc123"}
        mock_sp._post.assert_called_once_with(
            "playlists/playlist123/items",
            body={"uris": uris, "position": 0},
        )

    def test_playlist_add_items_caps_at_100(self, api):
        uris = [f"spotify:track:t{i}" for i in range(101)]
        with pytest.raises(ValueError, match="max 100"):
            api.playlist_add_items("p1", uris)


# ────────────────────────────────────────────────────────────
# Artist workaround
# ────────────────────────────────────────────────────────────

class TestArtistWorkaround:
    def test_artist_top_tracks_via_albums(self, api, mock_sp):
        # Mock albums response
        mock_sp._get.side_effect = [
            # First call: artist's albums
            {"items": [
                {
                    "id": "album1",
                    "name": "Greatest Hits",
                    "popularity": 80,
                    "release_date": "2023-01-01",
                },
                {
                    "id": "album2",
                    "name": "Debut Album",
                    "popularity": 60,
                    "release_date": "2020-05-15",
                },
            ]},
            # Second call: album1's tracks
            {"items": [
                {
                    "id": "track1",
                    "name": "Hit Song",
                    "uri": "spotify:track:track1",
                    "popularity": 75,
                    "duration_ms": 200000,
                    "explicit": False,
                    "preview_url": None,
                },
            ]},
            # Third call: album2's tracks
            {"items": [
                {
                    "id": "track2",
                    "name": "Early Track",
                    "uri": "spotify:track:track2",
                    "popularity": 50,
                    "duration_ms": 180000,
                    "explicit": True,
                    "preview_url": None,
                },
            ]},
        ]

        tracks = api.artist_top_tracks_via_albums("artist123", max_albums=2)

        # Verify we got 2 tracks
        assert len(tracks) == 2
        # Verify sorting: album1 has higher popularity so track1 should be first
        assert tracks[0]["album_popularity"] == 80
        assert tracks[1]["album_popularity"] == 60
        # Verify enriched metadata
        assert tracks[0]["album_name"] == "Greatest Hits"
        assert tracks[0]["album_id"] == "album1"
        # Verify API calls went to correct endpoints
        assert mock_sp._get.call_count == 3
        first_call = mock_sp._get.call_args_list[0]
        assert first_call[0][0] == "artists/artist123/albums"

    def test_artist_top_tracks_deduplicates(self, api, mock_sp):
        # Same track appears in two albums - should be deduped
        mock_sp._get.side_effect = [
            {"items": [
                {"id": "a1", "name": "A1", "popularity": 70, "release_date": "2020-01-01"},
                {"id": "a2", "name": "A2", "popularity": 60, "release_date": "2021-01-01"},
            ]},
            {"items": [
                {"id": "same", "name": "Same Song", "uri": "spotify:track:same",
                 "popularity": 80, "duration_ms": 200000, "explicit": False, "preview_url": None},
            ]},
            {"items": [
                {"id": "same", "name": "Same Song", "uri": "spotify:track:same",
                 "popularity": 80, "duration_ms": 200000, "explicit": False, "preview_url": None},
            ]},
        ]
        tracks = api.artist_top_tracks_via_albums("artist1", max_albums=2)
        # Should be deduplicated to 1 track
        assert len(tracks) == 1
        assert tracks[0]["id"] == "same"


class TestSearch:
    def test_search_caps_limit_at_10(self, api, mock_sp, capsys):
        api.search_artists("indie", limit=50)
        call_args = mock_sp._get.call_args
        assert call_args[1]["limit"] == 10
        # Should print a warning
        captured = capsys.readouterr()
        assert "limit" in captured.out.lower() or "limit" in captured.err.lower()

    def test_search_normal_limit(self, api, mock_sp):
        api.search_artists("indie", limit=5)
        call_args = mock_sp._get.call_args
        assert call_args[1]["limit"] == 5
        assert call_args[0][0] == "search"
        assert call_args[1]["q"] == "indie"
        assert call_args[1]["type"] == "artist"


# ────────────────────────────────────────────────────────────
# Critical regression: verify we're using NEW endpoints
# ────────────────────────────────────────────────────────────

class TestEndpointCorrectness:
    """These tests exist to catch regressions if someone uses old paths."""

    def test_never_use_old_playlist_tracks_path(self, api, mock_sp):
        """If we accidentally call /playlists/{id}/tracks (old path),
        the API will return 404."""
        mock_sp._get.return_value = {"items": []}
        api.playlist_items("xyz")
        call_path = mock_sp._get.call_args[0][0]
        assert "/tracks" not in call_path, \
            f"BUG: Using removed endpoint {call_path}, should be /items"

    def test_never_use_old_top_tracks_path(self, api, mock_sp):
        """artist_top_tracks was REMOVED in Feb 2026."""
        # Simulate API call to old endpoint returning 404
        def mock_get(path, **kwargs):
            if path == "artists/x/top-tracks":
                raise Exception("404 - endpoint removed")
            return {"items": []}
        mock_sp._get.side_effect = mock_get

        # Our workaround should NOT call /top-tracks
        mock_sp._get.side_effect = None
        mock_sp._get.return_value = {"items": []}
        api.artist_top_tracks_via_albums("x")

        # Verify no call was made to /top-tracks
        for call in mock_sp._get.call_args_list:
            assert "top-tracks" not in call[0][0], \
                f"BUG: Called removed endpoint {call[0][0]}"
