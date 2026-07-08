"""Unit tests for MusicBrainz client (FP-3b)."""
import json
import pytest
from unittest.mock import patch, MagicMock

from src.discovery.sources.musicbrainz import (
    MusicBrainzClient,
    MusicBrainzArtist,
    search_artist_by_name,
)


# ────────────────────────────────────────────────────────────
# Sample API responses
# ────────────────────────────────────────────────────────────

SAMPLE_ARTIST_FULL = {
    "id": "a74b1b7f-71a5-4011-9441-d0b5e4122711",
    "name": "Radiohead",
    "sort-name": "Radiohead",
    "type": "Group",
    "area": {
        "id": "8a754a16-0027-3a29-b6d7-2b40ea2501dd",
        "name": "United Kingdom",
    },
    "life-span": {
        "ended": "false",
        "begin": "1985",
    },
    "tag-list": [
        {"name": "rock", "count": 100},
        {"name": "electronic", "count": 80},
        {"name": "post-rock", "count": 60},
        {"name": "experimental", "count": 50},
        {"name": "alternative rock", "count": 40},
    ],
    "genre-list": [
        {"name": "alternative rock", "count": 5},
        {"name": "art rock", "count": 4},
        {"name": "electronic", "count": 3},
    ],
}

SAMPLE_SEARCH_RESULT = {
    "artist-list": [
        {
            "id": "abc-123",
            "name": "Phoebe Bridgers",
            "sort-name": "Bridgers, Phoebe",
        },
        {
            "id": "def-456",
            "name": "Phoebe Ryan",
            "sort-name": "Ryan, Phoebe",
        },
    ],
}


# ────────────────────────────────────────────────────────────
# Parsing tests
# ────────────────────────────────────────────────────────────

class TestParsing:
    def test_parse_full_artist(self):
        client = MusicBrainzClient()
        parsed = client._parse_artist(SAMPLE_ARTIST_FULL)
        assert parsed.name == "Radiohead"
        assert parsed.mbid == "a74b1b7f-71a5-4011-9441-d0b5e4122711"
        assert parsed.artist_type == "Group"
        assert parsed.country == "United Kingdom"
        assert parsed.begin_year == 1985
        assert parsed.end_year is None  # "ended": "false"
        # Tags sorted by count desc
        assert parsed.tags[0] == ("rock", 100)
        assert parsed.tags[1] == ("electronic", 80)
        assert "post-rock" in [t[0] for t in parsed.tags]
        # Genres
        assert "alternative rock" in parsed.genres
        assert "electronic" in parsed.genres

    def test_parse_minimal_search_result(self):
        client = MusicBrainzClient()
        parsed = client._parse_artist(
            {"id": "abc-123", "name": "Phoebe Bridgers"},
            minimal=True,
        )
        assert parsed.name == "Phoebe Bridgers"
        assert parsed.mbid == "abc-123"
        # Minimal mode skips everything else
        assert parsed.tags == []
        assert parsed.genres == []
        assert parsed.country == ""

    def test_parse_handles_missing_fields(self):
        client = MusicBrainzClient()
        parsed = client._parse_artist({})
        assert parsed.name == ""
        assert parsed.mbid == ""
        assert parsed.tags == []
        assert parsed.genres == []
        assert parsed.begin_year is None
        assert parsed.end_year is None

    def test_parse_handles_end_year(self):
        client = MusicBrainzClient()
        raw = {
            "id": "x", "name": "Disbanded Band",
            "life-span": {"ended": "true", "begin": "1990", "end": "2005-08"},
        }
        parsed = client._parse_artist(raw)
        assert parsed.begin_year == 1990
        assert parsed.end_year == 2005

    def test_parse_handles_no_lifespan(self):
        client = MusicBrainzClient()
        parsed = client._parse_artist({"id": "x", "name": "Y"})
        assert parsed.begin_year is None
        assert parsed.end_year is None

    def test_parse_handles_no_area(self):
        client = MusicBrainzClient()
        parsed = client._parse_artist({"id": "x", "name": "Y"})
        assert parsed.country == ""


# ────────────────────────────────────────────────────────────
# API method tests (with mocked musicbrainzngs)
# ────────────────────────────────────────────────────────────

class TestGetArtistByMbid:
    def test_success(self, tmp_path):
        client = MusicBrainzClient(cache_dir=tmp_path)
        with patch.object(client, "_call", return_value={"artist": SAMPLE_ARTIST_FULL}) as mock_call:
            artist = client.get_artist_by_mbid("a74b1b7f-71a5-4011-9441-d0b5e4122711")
        assert artist.name == "Radiohead"
        assert artist.country == "United Kingdom"
        # Verify it requested tags + genres includes
        mock_call.assert_called_once_with(
            "get_artist_by_id",
            "a74b1b7f-71a5-4011-9441-d0b5e4122711",
            includes=["tags", "genres"],
        )

    def test_empty_mbid_returns_none(self, tmp_path):
        client = MusicBrainzClient(cache_dir=tmp_path)
        assert client.get_artist_by_mbid("") is None
        assert client.get_artist_by_mbid("   ") is None

    def test_not_found_returns_none(self, tmp_path):
        client = MusicBrainzClient(cache_dir=tmp_path)
        with patch.object(client, "_call", return_value={}):
            artist = client.get_artist_by_mbid("invalid-id")
        assert artist is None  # empty artist dict -> name=""

    def test_response_error_returns_none(self, tmp_path):
        import musicbrainzngs
        client = MusicBrainzClient(cache_dir=tmp_path)
        with patch.object(client, "_call",
                          side_effect=musicbrainzngs.ResponseError("Not Found")):
            artist = client.get_artist_by_mbid("invalid")
        assert artist is None


class TestSearch:
    def test_search_artists(self, tmp_path):
        client = MusicBrainzClient(cache_dir=tmp_path)
        with patch.object(client, "_call", return_value=SAMPLE_SEARCH_RESULT) as mock_call:
            results = client.search_artists("phoebe", limit=10)
        assert len(results) == 2
        assert results[0].name == "Phoebe Bridgers"
        assert results[0].mbid == "abc-123"
        mock_call.assert_called_once_with("search_artists", query="phoebe", limit=10, offset=0)

    def test_search_caps_limit_at_100(self, tmp_path):
        client = MusicBrainzClient(cache_dir=tmp_path)
        with patch.object(client, "_call", return_value={"artist-list": []}) as mock_call:
            client.search_artists("test", limit=500)
        assert mock_call.call_args.kwargs["limit"] == 100

    def test_search_by_tag(self, tmp_path):
        client = MusicBrainzClient(cache_dir=tmp_path)
        with patch.object(client, "_call", return_value={"artist-list": []}) as mock_call:
            client.search_artists_by_tag("post-rock", limit=20)
        assert mock_call.call_args.kwargs["query"] == 'tag:"post-rock"'
        assert mock_call.call_args.kwargs["limit"] == 20

    def test_search_by_genre(self, tmp_path):
        client = MusicBrainzClient(cache_dir=tmp_path)
        with patch.object(client, "_call", return_value={"artist-list": []}) as mock_call:
            client.search_artists_by_genre("dream pop", limit=15)
        assert mock_call.call_args.kwargs["query"] == 'genre:"dream pop"'

    def test_search_by_country(self, tmp_path):
        client = MusicBrainzClient(cache_dir=tmp_path)
        with patch.object(client, "_call", return_value={"artist-list": []}) as mock_call:
            client.search_artists_by_country("FI", limit=10)
        assert mock_call.call_args.kwargs["query"] == 'area:"FI"'


# ────────────────────────────────────────────────────────────
# Caching tests
# ────────────────────────────────────────────────────────────

class TestCaching:
    def test_caches_response(self, tmp_path):
        """Second call should be served from cache."""
        client = MusicBrainzClient(cache_dir=tmp_path, cache_ttl_seconds=3600)
        # Mock the underlying musicbrainzngs function, NOT _call itself
        call_count = 0
        def fake_get_artist(mbid, includes=None):
            nonlocal call_count
            call_count += 1
            return {"artist": SAMPLE_ARTIST_FULL}
        with patch("src.discovery.sources.musicbrainz.musicbrainzngs.get_artist_by_id",
                   side_effect=fake_get_artist), \
             patch.object(client, "_throttle"):
            client.get_artist_by_mbid("test-mbid")
            client.get_artist_by_mbid("test-mbid")
        assert call_count == 1  # second call cached

    def test_cache_expires(self, tmp_path):
        import time
        client = MusicBrainzClient(cache_dir=tmp_path, cache_ttl_seconds=0)
        call_count = 0
        def fake_get_artist(mbid, includes=None):
            nonlocal call_count
            call_count += 1
            return {"artist": SAMPLE_ARTIST_FULL}
        with patch("src.discovery.sources.musicbrainz.musicbrainzngs.get_artist_by_id",
                   side_effect=fake_get_artist), \
             patch.object(client, "_throttle"):
            client.get_artist_by_mbid("test-mbid")
            time.sleep(0.01)
            client.get_artist_by_mbid("test-mbid")
        assert call_count == 2

    def test_throttle_called(self, tmp_path):
        """Verify throttle is called between requests."""
        client = MusicBrainzClient(cache_dir=tmp_path)
        with patch.object(client, "_throttle") as mock_throttle, \
             patch("src.discovery.sources.musicbrainz.musicbrainzngs.get_artist_by_id",
                   return_value={"artist": SAMPLE_ARTIST_FULL}):
            client.get_artist_by_mbid("x")
        mock_throttle.assert_called()


# ────────────────────────────────────────────────────────────
# Helper / convenience tests
# ────────────────────────────────────────────────────────────

class TestHelpers:
    def test_get_top_tags(self):
        artist = MusicBrainzArtist(
            name="X", mbid="x",
            tags=[("rock", 100), ("indie", 50), ("pop", 10)],
        )
        client = MusicBrainzClient()
        top = client.get_top_tags(artist, n=2)
        assert top == ["rock", "indie"]

    def test_get_top_tags_more_than_available(self):
        artist = MusicBrainzArtist(
            name="X", mbid="x",
            tags=[("rock", 100)],
        )
        client = MusicBrainzClient()
        assert client.get_top_tags(artist, n=10) == ["rock"]

    def test_search_artist_by_name_helper(self, tmp_path):
        with patch("src.discovery.sources.musicbrainz.MusicBrainzClient") as MockClient:
            instance = MockClient.return_value
            instance.search_artists.return_value = [
                MusicBrainzArtist(name="Test", mbid="x")
            ]
            results = search_artist_by_name("Test")
        assert len(results) == 1
        instance.search_artists.assert_called_once_with("Test", limit=5)


# ────────────────────────────────────────────────────────────
# User-Agent required by MusicBrainz
# ────────────────────────────────────────────────────────────

class TestUserAgent:
    def test_user_agent_set_on_init(self, tmp_path):
        # musicbrainzngs.set_useragent sets internal state but doesn't expose
        # the value until a request is made. We just verify the call doesn't
        # raise — full User-Agent verification requires a real request.
        MusicBrainzClient(cache_dir=tmp_path)  # should not raise
        # Verify USER_AGENT tuple format is correct
        from src.discovery.sources.musicbrainz import USER_AGENT
        assert USER_AGENT == ("spotify-curator", "0.2.0",
                             "https://github.com/petekaik/spotify-curator")
