# CLAUDE.md

Instructions for AI coding agents (Claude Code locally, and the Claude GitHub workflow) working in
this repo. Humans: the same rules apply to you.

## Commands

```bash
pip install -e ".[dev]" && pre-commit install   # setup
pytest --cov                                    # tests + 80% coverage gate
ruff check src tests && ruff format src tests   # lint + format
spotify-curator --help                          # CLI
```

## Layout

- `src/spotify/`: OAuth and the Spotify Web API wrapper (`api_v2.py`)
- `src/analyzer/`: taste profile from top artists/tracks, Parquet cache
- `src/discovery/`: sources (Last.fm, ListenBrainz, MusicBrainz, Reddit) and `ranking.py`
- `src/playlist/`: resolve candidates, pick tracks, shuffle, write to Spotify
- `src/cli/`: Typer CLI; `src/hermes_skill/`: the same pipeline as agent tools; `src/daemon/`: scheduler (digest is a stub)
- `src/config.py`: the only place that loads `.env` and defines `DATA_DIR`

## Rules

1. **Work from the backlog.** Features are FP items in `docs/FP_PLAN.md`, ranked by WSJF. New work
   starts as a GitHub issue (Feature form) with acceptance criteria.
2. **Test first.** Each acceptance criterion gets a test. Tests never call real APIs: mock
   `spotipy`/`requests`, or use the vcrpy cassettes in `tests/fixtures/cassettes/`.
3. **Runtime data goes through `DATA_DIR`.** Never `Path.home()` in modules; `tests/conftest.py`
   points `DATA_DIR` at a temp dir so tests cannot touch real user data.
4. **Spotify API is the February 2026 version.** Write calls pass `payload=` (not `body=`); artist
   `genres` and `popularity` are gone, `/artists/{id}/top-tracks` is removed. Verify a new endpoint
   with a live call before relying on it, and say in the PR that you did.
5. **No secrets in the repo.** Credentials come from `.env` or the environment. A pre-commit secret
   scan blocks hard-coded keys and machine-specific paths.
6. **Keep docs true.** Update `CHANGELOG.md` (Unreleased) and the FP status table when behavior
   changes. Don't claim a feature works until it has run end to end.
7. **Small, reviewable PRs.** Fill in the PR template, including what the agent did and what a
   human decided.

## Release

Bump `version` in `pyproject.toml`, move Unreleased notes under the new version in `CHANGELOG.md`,
then tag `vX.Y.Z` and push the tag. The Release workflow publishes the notes.
