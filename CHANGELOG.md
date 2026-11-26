# Changelog

All notable changes are listed here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/). Pushing a `vX.Y.Z` tag publishes the
matching section below as a GitHub release.

## [Unreleased]

### Fixed
- Tuning: a string for `preferred_geo_tags` or `mood_tags` is rejected (it was saved and matched
  letter by letter), and `reset` with an invalid change no longer wipes the saved tuning.

## [0.3.0] - 2026-11-26

### Added
- MCP server (`spotify-curator-mcp`): the agent tools for Claude, Codex, OpenClaw, Hermes and any
  MCP client, over stdio or streamable HTTP with bearer-token auth; `mcp` service in
  `deploy/compose.yml`.
- Persistent ranking tuning (`$SPOTIFY_CURATOR_HOME/tuning.json`) with `get_tuning` /
  `update_tuning` tools; mood seed tags and preferred geo tags are tunable.
- `dry_run` for mood playlists.

### Fixed
- Hermes tools failed at runtime: a lint autofix had removed their lazy imports. A test now guards it.

## [0.2.0] - 2026-11-26

### Added
- Docker deployment (`deploy/`) running the Hermes Agent with a `spotify-curator` skill and 6 tools.
- Discovery sources: MusicBrainz (FP-3b) and Reddit community mining (FP-3d).
- Recorded-response integration tests with vcrpy (FP-7).
- Daemon scaffolding: scheduler, weekly-digest workflow and file-based IPC (FP-9; the digest is
  still a stub).
- `spotify-curator init` creates a starter `.env`.
- Profile genres from Last.fm top tags, since Spotify no longer returns artist genres for this app.
- Last.fm tag results enriched with tags and listener counts via `artist.getInfo`.
- QA and DevOps automation: ruff lint and format, coverage gate (80%), Python 3.12/3.13 matrix,
  Docker build check, CodeQL, Dependabot, pre-commit, issue forms, PR template, tag-driven releases,
  Claude Code PR review.

### Changed
- One runtime config (`src/config.py`): `.env` loading and `DATA_DIR`
  (`$SPOTIFY_CURATOR_HOME` or `~/.spotify-curator`) shared by every module.
- Artist resolution requires an exact name match on Spotify.
- Playlist items are appended in order (no more `position: 0`).
- Packaging from `pyproject.toml` only; `requirements.txt` removed.

### Fixed
- Spotify write calls sent their JSON body as query parameters (`body=` → `payload=`).
- The Docker container ignored `SPOTIPY_CACHE_PATH` and `SPOTIPY_OPEN_BROWSER`, so it never found
  its token.
- Last.fm API key was read from `src/.env`, a path that does not exist.
- Tests overwrote the real user profile in `~/.spotify-curator`.
- An integration test used an absolute path from the author's machine, which broke CI.
- `PlaylistTrack.artist_name` held the album name.
- A failing daemon command was retried forever.

## [0.1.0] - 2026-07-08

### Added
- OAuth authorization-code flow with token cache and a wrapper for the February 2026 Spotify Web API (FP-1, FP-1b).
- Taste profile from top artists and tracks over three time ranges, cached as Parquet (FP-2).
- Discovery from Last.fm and ListenBrainz (FP-3).
- Ranking that rewards genre fit and penalises popularity (FP-4).
- Playlist builder with no two consecutive tracks by the same artist (FP-5).
- Typer CLI: `auth`, `status`, `profile`, `discover`, `playlist` (FP-6).

[Unreleased]: https://github.com/petekaik/spotify-curator/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/petekaik/spotify-curator/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/petekaik/spotify-curator/releases/tag/v0.2.0
[0.1.0]: https://github.com/petekaik/spotify-curator/tree/fb0286b
