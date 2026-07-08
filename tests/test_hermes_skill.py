"""Tests for the Hermes skill tools (FP-Hermes-integration).

These tests mock the underlying spotify-curator modules to verify that
the tool layer correctly:
- Forwards parameters
- Returns structured JSON
- Handles errors gracefully (no exceptions, just {"error": ...})
- Validates inputs (e.g. mood whitelist)
"""
import json
import pytest
from unittest.mock import patch, MagicMock

from src.hermes_skill.tools import (
    spotify_curator_status,
    spotify_curator_refresh_profile,
    spotify_curator_discover,
    spotify_curator_generate_mood,
    spotify_curator_generate_weekly,
    spotify_curator_get_reports,
    TOOL_REGISTRY,
    VALID_MOODS,
)


# ────────────────────────────────────────────────────────────
# Tool 1: status
# ────────────────────────────────────────────────────────────

class TestStatus:
    def test_status_all_healthy(self):
        with patch("src.hermes_skill.tools._import_spotify_curator") as mock_import:
            mods = {
                "is_authenticated": lambda: True,
                "get_current_user_id": lambda: "user123",
                "profile_exists": lambda p: True,
                "profile_age_hours": lambda p: 3.5,
                "PROFILE_PATH": "/tmp/profile",
            }
            mock_import.return_value = mods
            with patch("os.getenv", return_value="real-api-key-here"):
                result = spotify_curator_status()
        assert result["authenticated"] is True
        assert result["user_id"] == "user123"
        assert result["profile_built"] is True
        assert result["profile_age_hours"] == 3.5
        assert result["lastfm_configured"] is True
        assert result["tools_healthy"] is True

    def test_status_not_authenticated(self):
        with patch("src.hermes_skill.tools._import_spotify_curator") as mock_import:
            mods = {
                "is_authenticated": lambda: False,
                "get_current_user_id": lambda: None,
                "profile_exists": lambda p: False,
                "profile_age_hours": lambda p: None,
                "PROFILE_PATH": "/tmp",
            }
            mock_import.return_value = mods
            with patch("os.getenv", return_value=""):
                result = spotify_curator_status()
        assert result["authenticated"] is False
        assert result["profile_built"] is False
        assert result["lastfm_configured"] is False
        assert result["tools_healthy"] is False

    def test_status_returns_error_on_exception(self):
        with patch("src.hermes_skill.tools._import_spotify_curator",
                   side_effect=Exception("boom")):
            result = spotify_curator_status()
        assert "error" in result
        assert result["error"] == "status_check_failed"


# ────────────────────────────────────────────────────────────
# Tool 2: refresh_profile
# ────────────────────────────────────────────────────────────

class TestRefreshProfile:
    def test_refresh_profile_success(self):
        from datetime import datetime
        from src.analyzer.types import UserProfile
        mock_profile = UserProfile(
            user_id="u1",
            updated_at=datetime.now(),
            genre_weights={"indie": 0.5, "rock": 0.3},
            artist_weights={"a1": 1.0},
            total_artists=42,
            total_genres=8,
            total_tracks=120,
        )
        with patch("src.hermes_skill.tools._import_spotify_curator") as mock_import:
            mods = {
                "is_authenticated": lambda: True,
                "get_spotify_client": MagicMock(),
                "SpotifyAPIv2": MagicMock(),
                "ProfileBuilder": MagicMock(return_value=MagicMock(
                    build_and_save=MagicMock(return_value=mock_profile)
                )),
            }
            mock_import.return_value = mods
            result = spotify_curator_refresh_profile()
        assert result["success"] is True
        assert result["artists_count"] == 42
        assert result["genres_count"] == 8
        assert "indie" in result["top_genres"]
        assert "duration_seconds" in result

    def test_refresh_profile_not_authenticated(self):
        with patch("src.hermes_skill.tools._import_spotify_curator") as mock_import:
            mods = {"is_authenticated": lambda: False}
            mock_import.return_value = mods
            result = spotify_curator_refresh_profile()
        assert result["error"] == "not_authenticated"

    def test_refresh_profile_audio_features_false(self):
        """include_audio_features=False should be passed through."""
        from datetime import datetime
        from src.analyzer.types import UserProfile
        mock_profile = UserProfile(
            user_id="u1", updated_at=datetime.now(),
            genre_weights={}, artist_weights={},
            total_artists=10, total_genres=2, total_tracks=20,
        )
        captured = {}
        def mock_build_and_save(include_features):
            captured["include_features"] = include_features
            return mock_profile
        with patch("src.hermes_skill.tools._import_spotify_curator") as mock_import:
            mods = {
                "is_authenticated": lambda: True,
                "get_spotify_client": MagicMock(),
                "SpotifyAPIv2": MagicMock(),
                "ProfileBuilder": MagicMock(return_value=MagicMock(
                    build_and_save=mock_build_and_save
                )),
            }
            mock_import.return_value = mods
            spotify_curator_refresh_profile(include_audio_features=False)
        assert captured["include_features"] is False


# ────────────────────────────────────────────────────────────
# Tool 3: discover
# ────────────────────────────────────────────────────────────

class TestDiscover:
    def test_discover_success(self):
        from datetime import datetime
        from src.analyzer.types import UserProfile
        from src.discovery.sources.lastfm import LastfmArtist
        from src.discovery.ranking import Candidate
        profile = UserProfile(
            user_id="u1", updated_at=datetime.now(),
            genre_weights={"indie": 1.0},
        )
        with patch("src.hermes_skill.tools._import_spotify_curator") as mock_import:
            mods = {
                "profile_exists": lambda p: True,
                "load_profile": lambda p: profile,
                "PROFILE_PATH": "/tmp",
                "LastfmClient": MagicMock(return_value=MagicMock(
                    _api_key="fake",
                    tag_get_top_artists=MagicMock(return_value=[
                        LastfmArtist(name="Artist 1", listeners=1000, mbid="m1", tags=["indie"]),
                    ]),
                )),
                "Candidate": Candidate,  # use the real class
                "deduplicate_candidates": lambda c: c,
                "rank_artists": lambda cands, prof, limit: [
                    (cands[0], 0.85, {"genre_match": 0.5, "emerging_signal": 0.3}),
                ],
            }
            mock_import.return_value = mods
            result = spotify_curator_discover(tag="indie", period="6month")
        assert result["tag"] == "indie"
        assert result["candidates_count"] == 1
        assert result["ranked"][0]["name"] == "Artist 1"
        assert result["ranked"][0]["score"] == 0.85
        assert "score_breakdown" in result["ranked"][0]

    def test_discover_no_profile(self):
        with patch("src.hermes_skill.tools._import_spotify_curator") as mock_import:
            mods = {
                "profile_exists": lambda p: False,
                "PROFILE_PATH": "/tmp",
            }
            mock_import.return_value = mods
            result = spotify_curator_discover()
        assert result["error"] == "profile_not_built"

    def test_discover_no_lastfm_key(self):
        from datetime import datetime
        from src.analyzer.types import UserProfile
        profile = UserProfile(user_id="u1", updated_at=datetime.now())
        with patch("src.hermes_skill.tools._import_spotify_curator") as mock_import:
            mods = {
                "profile_exists": lambda p: True,
                "load_profile": lambda p: profile,
                "PROFILE_PATH": "/tmp",
                "LastfmClient": MagicMock(return_value=MagicMock(_api_key="")),
            }
            mock_import.return_value = mods
            result = spotify_curator_discover()
        assert result["error"] == "lastfm_not_configured"


# ────────────────────────────────────────────────────────────
# Tool 4: generate_mood
# ────────────────────────────────────────────────────────────

class TestGenerateMood:
    def test_invalid_mood(self):
        result = spotify_curator_generate_mood(mood="nervous")
        assert result["error"] == "invalid_mood"
        assert "calming" in result["details"]  # lists valid options

    def test_not_authenticated(self):
        with patch("src.hermes_skill.tools._import_spotify_curator") as mock_import:
            mods = {"is_authenticated": lambda: False}
            mock_import.return_value = mods
            result = spotify_curator_generate_mood(mood="calming")
        assert result["error"] == "not_authenticated"

    def test_generate_mood_success(self):
        from datetime import datetime
        from src.analyzer.types import UserProfile
        from src.playlist.builder import BuiltPlaylist
        profile = UserProfile(
            user_id="u1", updated_at=datetime.now(),
            genre_weights={"ambient": 1.0},
        )
        with patch("src.hermes_skill.tools._import_spotify_curator") as mock_import:
            mods = {
                "is_authenticated": lambda: True,
                "profile_exists": lambda p: True,
                "load_profile": lambda p: profile,
                "PROFILE_PATH": "/tmp",
                "LastfmClient": MagicMock(return_value=MagicMock(
                    _api_key="fake",
                    tag_get_top_artists=MagicMock(return_value=[]),
                )),
                "get_spotify_client": MagicMock(),
                "SpotifyAPIv2": MagicMock(),
                "PlaylistBuilder": MagicMock(),
            }
            mock_import.return_value = mods
            result = spotify_curator_generate_mood(mood="calming", n_artists=10)
        # Empty results -> no_artists_found (because mocked returns [])
        assert "error" in result
        assert result["error"] == "no_artists_found"

    def test_generate_mood_full_flow(self):
        from datetime import datetime
        from src.analyzer.types import UserProfile
        from src.discovery.sources.lastfm import LastfmArtist
        from src.discovery.ranking import Candidate
        from src.playlist.builder import BuiltPlaylist
        profile = UserProfile(
            user_id="u1", updated_at=datetime.now(),
            genre_weights={"ambient": 1.0},
        )
        c1 = Candidate(name="A1", lastfm_listeners=1000, lastfm_tags=["ambient"], mbid="m1")
        c2 = Candidate(name="A2", lastfm_listeners=2000, lastfm_tags=["ambient"], mbid="m2")
        c3 = Candidate(name="A3", lastfm_listeners=3000, lastfm_tags=["ambient"], mbid="m3")
        mock_playlist = BuiltPlaylist(
            name="Test", description="",
            tracks=[],
            candidate_count=3,
            total_artist_count=3,
            spotify_playlist_id="pl-123",
        )
        with patch("src.hermes_skill.tools._import_spotify_curator") as mock_import:
            mods = {
                "is_authenticated": lambda: True,
                "profile_exists": lambda p: True,
                "load_profile": lambda p: profile,
                "PROFILE_PATH": "/tmp",
                "LastfmClient": MagicMock(return_value=MagicMock(
                    _api_key="fake",
                    tag_get_top_artists=MagicMock(return_value=[
                        LastfmArtist(name="A1", listeners=1000, mbid="m1", tags=["ambient"]),
                        LastfmArtist(name="A2", listeners=2000, mbid="m2", tags=["ambient"]),
                        LastfmArtist(name="A3", listeners=3000, mbid="m3", tags=["ambient"]),
                    ]),
                )),
                "get_spotify_client": MagicMock(),
                "SpotifyAPIv2": MagicMock(),
                "PlaylistBuilder": MagicMock(return_value=MagicMock(
                    build=MagicMock(return_value=mock_playlist),
                    write_to_spotify=MagicMock(return_value=mock_playlist),
                )),
                "Candidate": Candidate,  # use real class
                "deduplicate_candidates": lambda c: c,
                "rank_artists": lambda cands, prof, limit: [
                    (c, 0.9, {}) for c in cands[:limit]
                ],
            }
            mock_import.return_value = mods
            result = spotify_curator_generate_mood(mood="calming", n_artists=3)
        assert result["success"] is True
        assert result["mood"] == "calming"
        assert "open.spotify.com/playlist/pl-123" in result["playlist_url"]
        assert "A1" in result["artists"]
        assert result["mood"] == "calming"
        assert "open.spotify.com/playlist/pl-123" in result["playlist_url"]
        assert "A1" in result["artists"]


# ────────────────────────────────────────────────────────────
# Tool 5: generate_weekly
# ────────────────────────────────────────────────────────────

class TestGenerateWeekly:
    def test_generate_weekly_runs_all_moods(self):
        with patch("src.hermes_skill.tools.spotify_curator_generate_mood") as mock_mood:
            mock_mood.return_value = {
                "success": True,
                "mood": "calming",
                "playlist_url": "https://open.spotify.com/playlist/x",
                "playlist_name": "Test",
                "artists_count": 10,
                "tracks_count": 20,
            }
            result = spotify_curator_generate_weekly()
        # Should call all 6 moods
        assert mock_mood.call_count == 6
        assert len(result["playlists"]) == 6
        assert result["total_artists"] == 60
        assert result["total_tracks"] == 120

    def test_generate_weekly_skips_failed_moods(self):
        with patch("src.hermes_skill.tools.spotify_curator_generate_mood") as mock_mood:
            # Side-effect based on mood argument
            def fake_gen(mood, **_):
                if mood in ("cheerful", "stimulating", "energetic"):
                    return {
                        "success": True, "mood": mood,
                        "playlist_url": f"https://open.spotify.com/playlist/{mood}",
                        "playlist_name": f"Weekly {mood}",
                        "artists_count": 10, "tracks_count": 20,
                        "artists": ["x"],
                    }
                return {"error": f"{mood}_failed"}
            mock_mood.side_effect = fake_gen
            result = spotify_curator_generate_weekly()
        # 3 of 6 succeeded
        assert len(result["playlists"]) == 3
        assert {p["mood"] for p in result["playlists"]} == {"cheerful", "stimulating", "energetic"}


# ────────────────────────────────────────────────────────────
# Tool 6: get_reports
# ────────────────────────────────────────────────────────────

class TestGetReports:
    def test_no_reports_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SPOTIFY_CURATOR_HOME", str(tmp_path))
        result = spotify_curator_get_reports()
        assert result["runs"] == []
        assert "daemon" in result["details"].lower()

    def test_lists_reports(self, tmp_path, monkeypatch):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        # Create 2 reports
        for week in ["2026-W27", "2026-W28"]:
            run_dir = reports_dir / week
            run_dir.mkdir()
            (run_dir / "playlists.json").write_text(json.dumps({
                "generated_at": f"{week[:4]}-{week[5:7]}-01T00:00:00Z",
                "playlists": [
                    {"mood": "calming", "url": f"https://open.spotify.com/playlist/{week}"}
                ],
            }))
        monkeypatch.setenv("SPOTIFY_CURATOR_HOME", str(tmp_path))
        result = spotify_curator_get_reports()
        assert len(result["runs"]) == 2
        # Most recent first
        assert result["runs"][0]["id"] == "2026-W28"

    def test_mood_filter(self, tmp_path, monkeypatch):
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        (reports_dir / "2026-W28").mkdir()
        (reports_dir / "2026-W28" / "playlists.json").write_text(json.dumps({
            "generated_at": "2026-07-08T00:00:00Z",
            "playlists": [{"mood": "calming", "url": "x"}],
        }))
        (reports_dir / "2026-W27").mkdir()
        (reports_dir / "2026-W27" / "playlists.json").write_text(json.dumps({
            "generated_at": "2026-07-01T00:00:00Z",
            "playlists": [{"mood": "energetic", "url": "y"}],
        }))
        monkeypatch.setenv("SPOTIFY_CURATOR_HOME", str(tmp_path))
        result = spotify_curator_get_reports(mood_filter="calming")
        assert len(result["runs"]) == 1
        assert result["runs"][0]["id"] == "2026-W28"


# ────────────────────────────────────────────────────────────
# Tool registry
# ────────────────────────────────────────────────────────────

class TestToolRegistry:
    def test_all_tools_registered(self):
        assert len(TOOL_REGISTRY) == 6
        for name in (
            "spotify_curator_status",
            "spotify_curator_refresh_profile",
            "spotify_curator_discover",
            "spotify_curator_generate_mood",
            "spotify_curator_generate_weekly",
            "spotify_curator_get_reports",
        ):
            assert name in TOOL_REGISTRY
            assert "function" in TOOL_REGISTRY[name]
            assert "schema" in TOOL_REGISTRY[name]
            assert TOOL_REGISTRY[name]["schema"]["name"] == name

    def test_all_schemas_have_description(self):
        for name, spec in TOOL_REGISTRY.items():
            assert "description" in spec["schema"], f"{name} missing description"
            assert len(spec["schema"]["description"]) > 10

    def test_generate_mood_schema_enums_moods(self):
        schema = TOOL_REGISTRY["spotify_curator_generate_mood"]["schema"]
        mood_prop = schema["parameters"]["properties"]["mood"]
        assert "enum" in mood_prop
        assert set(mood_prop["enum"]) == set(VALID_MOODS)

    def test_register_tools_function(self):
        from src.hermes_skill import register_tools
        mock_registry = MagicMock()
        register_tools(mock_registry)
        # Should have registered all 6 tools
        assert mock_registry.register.call_count == 6
        registered_names = {c.kwargs["name"] for c in mock_registry.register.call_args_list}
        assert "spotify_curator_status" in registered_names
        assert "spotify_curator_generate_weekly" in registered_names


# ────────────────────────────────────────────────────────────
# Error handling
# ────────────────────────────────────────────────────────────

class TestErrorHandling:
    def test_discover_handles_internal_exception(self):
        with patch("src.hermes_skill.tools._import_spotify_curator",
                   side_effect=RuntimeError("kaboom")):
            result = spotify_curator_discover()
        assert "error" in result
        assert result["error"] == "discover_failed"
        assert "kaboom" in result["details"]

    def test_refresh_profile_handles_spotify_4xx(self):
        with patch("src.hermes_skill.tools._import_spotify_curator") as mock_import:
            mods = {
                "is_authenticated": lambda: True,
                "get_spotify_client": MagicMock(),
                "SpotifyAPIv2": MagicMock(),
                "ProfileBuilder": MagicMock(return_value=MagicMock(
                    build_and_save=MagicMock(side_effect=RuntimeError("401 Unauthorized"))
                )),
            }
            mock_import.return_value = mods
            result = spotify_curator_refresh_profile()
        assert result["error"] == "profile_build_failed"
        assert "401" in result["details"]
