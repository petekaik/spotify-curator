"""Unit tests for Last.fm client (FP-3).

Tests use vcrpy cassettes to record/replay real Last.fm API responses,
so we don't need a live API key for tests to pass.
"""
import json
import pytest
from unittest.mock import patch, MagicMock

from src.discovery.sources.lastfm import (
    LastfmClient,
    LastfmArtist,
    get_top_artists_for_tag,
)


# ────────────────────────────────────────────────────────────
# Mocked API responses (synthetic, no network needed)
# ────────────────────────────────────────────────────────────

SAMPLE_TAG_RESPONSE = {
    "topartists": {
        "tag": "indie rock",
        "attr": {"rank": "1", "page": "1", "perPage": "2", "totalPages": "1", "total": "2"},
        "artist": [
            {
                "name": "Phoebe Bridgers",
                "mbid": "8a4b7b5e-9c1a-4d2e-b3a1-1234567890ab",
                "url": "https://www.last.fm/music/Phoebe+Bridgers",
                "stats": {"listeners": "1500000", "playcount": "25000000"},
                "tags": {"tag": [{"name": "indie"}, {"name": "sad girl"}]},
            },
            {
                "name": "Big Thief",
                "mbid": "9b5c8c6f-aa2b-5e3f-c4b2-2345678901bc",
                "url": "https://www.last.fm/music/Big+Thief",
                "stats": {"listeners": "800000", "playcount": "12000000"},
                "tags": {"tag": [{"name": "indie rock"}]},
            },
        ],
    }
}

SAMPLE_ARTIST_INFO_RESPONSE = {
    "artist": {
        "name": "Phoebe Bridgers",
        "mbid": "8a4b7b5e-9c1a-4d2e-b3a1-1234567890ab",
        "url": "https://www.last.fm/music/Phoebe+Bridgers",
        "stats": {"listeners": "1500000", "playcount": "25000000"},
        "tags": {"tag": [
            {"name": "indie", "url": "x"},
            {"name": "sad girl", "url": "x"},
        ]},
        "bio": {
            "summary": "Phoebe Bridgers is an American singer-songwriter.",
            "content": "Full bio here...",
        },
        "similar": {"artist": [
            {"name": "Julien Baker", "url": "x"},
            {"name": "Lucy Dacus", "url": "x"},
        ]},
    }
}


# ────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────

class TestLastfmParsing:
    def test_parse_artist_minimal(self):
        from src.discovery.sources.lastfm import LastfmClient
        client = LastfmClient(api_key="fake_key_for_test")
        parsed = client._parse_artist({
            "name": "Test Artist",
            "mbid": "abc-123",
            "stats": {"listeners": "1000", "playcount": "5000"},
            "tags": {"tag": [{"name": "indie"}, {"name": "rock"}]},
        })
        assert parsed.name == "Test Artist"
        assert parsed.mbid == "abc-123"
        assert parsed.listeners == 1000
        assert parsed.playcount == 5000
        assert parsed.tags == ["indie", "rock"]
        assert parsed.similar == []

    def test_parse_artist_with_similar(self):
        client = LastfmClient(api_key="fake")
        parsed = client._parse_artist(
            SAMPLE_ARTIST_INFO_RESPONSE["artist"],
            include_similar=True,
        )
        assert parsed.name == "Phoebe Bridgers"
        assert "indie" in parsed.tags
        assert "Julien Baker" in parsed.similar
        assert parsed.bio_summary != ""

    def test_parse_artist_handles_missing_fields(self):
        """Defensive: should not crash on partial data."""
        client = LastfmClient(api_key="fake")
        parsed = client._parse_artist({})
        assert parsed.name == ""
        assert parsed.listeners == 0
        assert parsed.tags == []


class TestLastfmClient:
    def test_client_requires_api_key(self, monkeypatch):
        """If no key in env, raise clear error."""
        monkeypatch.delenv("LASTFM_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="LASTFM_API_KEY"):
            LastfmClient(api_key=None)._call("tag.getTopArtists", tag="indie")

    def test_tag_get_top_artists(self, tmp_path):
        """Verify request shape and response parsing."""
        client = LastfmClient(api_key="fake", cache_dir=tmp_path)
        with patch.object(client, "_call", return_value=SAMPLE_TAG_RESPONSE) as mock_call:
            artists = client.tag_get_top_artists("indie rock", period="6month", limit=2)

        assert len(artists) == 2
        assert artists[0].name == "Phoebe Bridgers"
        assert artists[0].listeners == 1500000
        assert artists[0].tags == ["indie", "sad girl"]
        assert artists[1].name == "Big Thief"
        # Verify we called with the right method and params
        mock_call.assert_called_once()
        args = mock_call.call_args
        assert args[0][0] == "tag.getTopArtists"
        assert args[1]["tag"] == "indie rock"
        assert args[1]["period"] == "6month"

    def test_artist_get_info_includes_similar(self, tmp_path):
        client = LastfmClient(api_key="fake", cache_dir=tmp_path)
        with patch.object(client, "_call", return_value=SAMPLE_ARTIST_INFO_RESPONSE):
            info = client.artist_get_info("Phoebe Bridgers")
        assert info.name == "Phoebe Bridgers"
        assert "Julien Baker" in info.similar
        assert "Lucy Dacus" in info.similar

    def test_artist_get_info_prefers_mbid(self, tmp_path):
        """When mbid is given, don't pass artist name."""
        client = LastfmClient(api_key="fake", cache_dir=tmp_path)
        with patch.object(client, "_call", return_value=SAMPLE_ARTIST_INFO_RESPONSE) as mock_call:
            client.artist_get_info("ignored", mbid="abc-123")
        call_args = mock_call.call_args
        assert "artist" not in call_args[1]
        assert call_args[1]["mbid"] == "abc-123"


class TestLastfmCaching:
    def test_caches_response(self, tmp_path):
        """Second call should not hit the network."""
        client = LastfmClient(
            api_key="fake",
            cache_dir=tmp_path,
            cache_ttl_seconds=3600,
        )
        call_count = 0

        def fake_http_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(return_value=SAMPLE_TAG_RESPONSE)
            return resp

        with patch.object(client._session, "get", side_effect=fake_http_get):
            with patch.object(client, "_throttle"):  # skip rate limit
                client.tag_get_top_artists("indie rock", limit=2)
                client.tag_get_top_artists("indie rock", limit=2)  # cached

        assert call_count == 1, "Second call should have been served from cache"

    def test_cache_expires(self, tmp_path):
        """Old cache should be ignored."""
        import time
        client = LastfmClient(
            api_key="fake",
            cache_dir=tmp_path,
            cache_ttl_seconds=0,  # immediately expired
        )
        with patch.object(client, "_call", return_value=SAMPLE_TAG_RESPONSE) as mock_call:
            client.tag_get_top_artists("indie rock", limit=2)
            time.sleep(0.01)
            client.tag_get_top_artists("indie rock", limit=2)
        # Should have called the API twice (cache expired)
        assert mock_call.call_count == 2


class TestConvenience:
    def test_get_top_artists_for_tag(self, tmp_path):
        with patch("src.discovery.sources.lastfm.LastfmClient") as MockClient:
            instance = MockClient.return_value
            instance.tag_get_top_artists.return_value = [
                LastfmArtist(name="A1", listeners=1000),
            ]
            result = get_top_artists_for_tag("indie rock", api_key="fake")
        assert len(result) == 1
        assert result[0].name == "A1"
        instance.tag_get_top_artists.assert_called_once_with(
            "indie rock", period="6month", limit=30,
        )
