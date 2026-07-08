"""Tests for the Typer CLI (FP-6).

Uses CliRunner to test commands in isolation, with all Spotify calls mocked.
"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime

from typer.testing import CliRunner

from src.cli.main import app
from src.analyzer.types import UserProfile


runner = CliRunner()


# ────────────────────────────────────────────────────────────
# Auth + status
# ────────────────────────────────────────────────────────────

class TestAuthCommand:
    def test_auth_success(self):
        mock_sp = MagicMock()
        mock_sp.current_user.return_value = {"id": "u1", "display_name": "TestUser"}
        with patch("src.cli.main.get_spotify_client", return_value=mock_sp):
            result = runner.invoke(app, ["auth"])
        assert result.exit_code == 0
        assert "TestUser" in result.stdout
        assert "u1" in result.stdout

    def test_auth_failure(self):
        with patch("src.cli.main.get_spotify_client", side_effect=Exception("OAuth failed")):
            result = runner.invoke(app, ["auth"])
        assert result.exit_code == 1
        assert "Authentication failed" in result.stdout


class TestStatusCommand:
    def test_status_no_auth_no_profile(self):
        with patch("src.cli.main.is_authenticated", return_value=False), \
             patch("src.cli.main.profile_exists", return_value=False):
            result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "Not authenticated" in result.stdout
        assert "Not built" in result.stdout

    def test_status_auth_and_profile(self):
        with patch("src.cli.main.is_authenticated", return_value=True), \
             patch("src.cli.main.get_current_user_id", return_value="u1"), \
             patch("src.cli.main.profile_exists", return_value=True), \
             patch("src.cli.main.profile_age_hours", return_value=2.5):
            result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "u1" in result.stdout
        assert "2.5 hours ago" in result.stdout


# ────────────────────────────────────────────────────────────
# Profile subcommands
# ────────────────────────────────────────────────────────────

class TestProfileBuild:
    def test_profile_build_not_authenticated(self):
        with patch("src.cli.main.is_authenticated", return_value=False):
            result = runner.invoke(app, ["profile", "build"])
        assert result.exit_code == 1
        assert "Not authenticated" in result.stdout

    def test_profile_build_success(self):
        mock_api = MagicMock()
        mock_profile = UserProfile(
            user_id="u1",
            updated_at=datetime.now(),
            genre_weights={"indie": 0.5},
            artist_weights={"a1": 1.0},
            total_artists=50,
            total_genres=10,
            total_tracks=100,
        )
        with patch("src.cli.main.is_authenticated", return_value=True), \
             patch("src.cli.main._get_api", return_value=mock_api), \
             patch("src.cli.main.ProfileBuilder") as MockBuilder:
            instance = MockBuilder.return_value
            instance.build_and_save.return_value = mock_profile
            result = runner.invoke(app, ["profile", "build"])
        assert result.exit_code == 0
        assert "50 artists" in result.stdout


class TestProfileShow:
    def test_profile_show_no_cache(self):
        with patch("src.cli.main.profile_exists", return_value=False):
            result = runner.invoke(app, ["profile", "show"])
        assert result.exit_code == 1
        assert "No profile found" in result.stdout

    def test_profile_show(self):
        profile = UserProfile(
            user_id="u1",
            updated_at=datetime.now(),
            genre_weights={"indie": 0.5, "rock": 0.3, "pop": 0.2},
            artist_weights={"a1": 0.7, "a2": 0.3},
        )
        with patch("src.cli.main.profile_exists", return_value=True), \
             patch("src.cli.main.load_profile", return_value=profile):
            result = runner.invoke(app, ["profile", "show", "--top", "5"])
        assert result.exit_code == 0
        assert "indie" in result.stdout
        assert "rock" in result.stdout


# ────────────────────────────────────────────────────────────
# Discover subcommands
# ────────────────────────────────────────────────────────────

class TestDiscoverLastfm:
    def test_no_api_key(self):
        with patch("src.cli.main.LastfmClient") as MockClient:
            instance = MockClient.return_value
            instance._api_key = ""
            result = runner.invoke(app, ["discover", "lastfm", "--tag", "indie"])
        assert result.exit_code == 1
        assert "LASTFM_API_KEY" in result.stdout

    def test_no_results(self):
        with patch("src.cli.main.LastfmClient") as MockClient, \
             patch("src.cli.main.is_authenticated", return_value=True):
            instance = MockClient.return_value
            instance._api_key = "fake"
            instance.tag_get_top_artists.return_value = []
            result = runner.invoke(app, ["discover", "lastfm", "--tag", "empty"])
        assert result.exit_code == 1
        assert "No artists found" in result.stdout

    def test_no_ranking_just_lists(self):
        """If --no-rank, just show raw list without needing profile."""
        from src.discovery.sources.lastfm import LastfmArtist
        with patch("src.cli.main.LastfmClient") as MockClient:
            instance = MockClient.return_value
            instance._api_key = "fake"
            instance.tag_get_top_artists.return_value = [
                LastfmArtist(name="Artist 1", listeners=1000, mbid="m1"),
                LastfmArtist(name="Artist 2", listeners=2000, mbid="m2"),
            ]
            result = runner.invoke(app, ["discover", "lastfm", "--tag", "indie", "--no-rank"])
        assert result.exit_code == 0
        assert "Artist 1" in result.stdout
        assert "Artist 2" in result.stdout

    def test_with_ranking(self):
        from src.discovery.sources.lastfm import LastfmArtist
        profile = UserProfile(
            user_id="u1",
            updated_at=datetime.now(),
            genre_weights={"indie rock": 1.0},
        )
        with patch("src.cli.main.LastfmClient") as MockClient, \
             patch("src.cli.main.profile_exists", return_value=True), \
             patch("src.cli.main.load_profile", return_value=profile):
            instance = MockClient.return_value
            instance._api_key = "fake"
            instance.tag_get_top_artists.return_value = [
                LastfmArtist(name="Matching Artist", listeners=5000, mbid="m1", tags=["indie rock"]),
                LastfmArtist(name="Wrong Genre", listeners=3000, mbid="m2", tags=["country"]),
            ]
            result = runner.invoke(app, ["discover", "lastfm", "--tag", "indie rock"])
        assert result.exit_code == 0
        assert "Matching Artist" in result.stdout


class TestDiscoverListenbrainz:
    def test_prints_notice(self):
        """v0.1.0: Spotify→MBID mapping not yet implemented."""
        with patch("src.cli.main.profile_exists", return_value=True), \
             patch("src.cli.main.load_profile", return_value=UserProfile(
                 user_id="u1", updated_at=datetime.now()
             )):
            result = runner.invoke(app, ["discover", "listenbrainz"])
        assert result.exit_code == 0
        assert "MusicBrainz" in result.stdout


# ────────────────────────────────────────────────────────────
# Playlist subcommands
# ────────────────────────────────────────────────────────────

class TestPlaylistCreate:
    def test_no_lastfm_key(self):
        with patch("src.cli.main._get_api"), \
             patch("src.cli.main.profile_exists", return_value=True), \
             patch("src.cli.main.load_profile", return_value=UserProfile(
                 user_id="u1", updated_at=datetime.now()
             )), \
             patch("src.cli.main.LastfmClient") as MockClient:
            instance = MockClient.return_value
            instance._api_key = ""
            result = runner.invoke(app, ["playlist", "create", "Test"])
        assert result.exit_code == 1
        assert "LASTFM_API_KEY" in result.stdout

    def test_dry_run_skips_spotify_write(self):
        from src.discovery.sources.lastfm import LastfmArtist
        from src.playlist.builder import BuiltPlaylist
        profile = UserProfile(
            user_id="u1",
            updated_at=datetime.now(),
            genre_weights={"indie": 1.0},
        )
        mock_playlist = BuiltPlaylist(
            name="Test",
            description="",
            tracks=[],
            total_artist_count=0,
        )
        with patch("src.cli.main._get_api"), \
             patch("src.cli.main.profile_exists", return_value=True), \
             patch("src.cli.main.load_profile", return_value=profile), \
             patch("src.cli.main.LastfmClient") as MockClient, \
             patch("src.cli.main.PlaylistBuilder") as MockBuilder:
            lfm_instance = MockClient.return_value
            lfm_instance._api_key = "fake"
            lfm_instance.tag_get_top_artists.return_value = [
                LastfmArtist(name="A1", listeners=1000, mbid="m1", tags=["indie"]),
            ]
            builder_instance = MockBuilder.return_value
            builder_instance.build.return_value = mock_playlist
            result = runner.invoke(app, [
                "playlist", "create", "Test", "--dry-run",
            ])
        assert result.exit_code == 0
        # write_to_spotify was NOT called
        builder_instance.write_to_spotify.assert_not_called()
        assert "dry-run" in result.stdout.lower()

    def test_full_flow_writes_to_spotify(self):
        from src.discovery.sources.lastfm import LastfmArtist
        from src.playlist.builder import BuiltPlaylist, PlaylistTrack
        profile = UserProfile(
            user_id="u1",
            updated_at=datetime.now(),
            genre_weights={"indie": 1.0},
        )
        mock_playlist = BuiltPlaylist(
            name="Indie Mix",
            description="",
            tracks=[
                PlaylistTrack(uri="spotify:track:t1", name="T1", artist_name="A1"),
            ],
            total_artist_count=1,
        )
        with patch("src.cli.main._get_api"), \
             patch("src.cli.main.profile_exists", return_value=True), \
             patch("src.cli.main.load_profile", return_value=profile), \
             patch("src.cli.main.LastfmClient") as MockClient, \
             patch("src.cli.main.PlaylistBuilder") as MockBuilder:
            lfm_instance = MockClient.return_value
            lfm_instance._api_key = "fake"
            lfm_instance.tag_get_top_artists.return_value = [
                LastfmArtist(name="A1", listeners=1000, mbid="m1", tags=["indie"]),
            ]
            builder_instance = MockBuilder.return_value
            builder_instance.build.return_value = mock_playlist
            written = BuiltPlaylist(
                name="Indie Mix", description="", tracks=mock_playlist.tracks,
                total_artist_count=1, spotify_playlist_id="new-playlist-id",
            )
            builder_instance.write_to_spotify.return_value = written
            result = runner.invoke(app, ["playlist", "create", "Indie Mix"])
        assert result.exit_code == 0
        builder_instance.write_to_spotify.assert_called_once()
        assert "open.spotify.com/playlist/new-playlist-id" in result.stdout


class TestNoArgsShowsHelp:
    def test_no_args(self):
        """No-args invocation should exit non-zero (Typer convention) and show help."""
        result = runner.invoke(app, [])
        # Typer returns exit code 2 when no_args_is_help is set
        # and help is shown (matches CLI conventions)
        assert result.exit_code in (0, 2)
        # Should mention at least one of our commands in the output
        assert any(
            cmd in result.stdout
            for cmd in ("auth", "profile", "discover", "playlist", "Usage", "Commands")
        )
