# Spotify Curator

[![CI](https://github.com/petekaik/spotify-curator/actions/workflows/ci.yml/badge.svg)](https://github.com/petekaik/spotify-curator/actions/workflows/ci.yml)
[![Tests](https://img.shields.io/badge/tests-31%20passing-brightgreen)](tests/)
[![Python](https://img.shields.io/badge/python-3.12-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**A truly intelligent playlist designer** that finds small and emerging
artists based on the user's listening preferences — without the
recommendation-bubble effect.

> **Status:** v0.1.0-dev — FP-0/1/1b/2 ✅ | FP-3 next

## 🎯 Vision

Spotify's own "Discover Weekly" and "Release Radar" recommend primarily
artists who are known to work — i.e. popular. This project flips the
logic around: **the less heard, the more interesting the discovery** —
> **Status:** v0.2.0-dev — Docker + Hermes skill + tools ready, **FP-3b next**

## 🧩 What's new in v0.2.0

- ✅ **Docker deployment** — `deploy/Dockerfile` + `deploy/compose.yml`
  - Hermes Agent as the runtime inside the container
  - Bind-mount volumes for live-edit dev
  - Headless OAuth via `deploy/scripts/auth_on_host.sh` (run on Mac, not in container)
  - `HERMES_PROFILE=spotify-curator` for isolated config
  - Port-mapped: 8642 (Hermes API), 8788 (future IPC)
  - **Portable to QNAP** — same compose works on any Docker host
- ✅ **Hermes skill** — `skills/spotify-curator/SKILL.md`
  - 6 tools exposed to other Hermes agents:
    - `spotify_curator_status` — auth + profile health check
    - `spotify_curator_refresh_profile` — rebuild taste profile
    - `spotify_curator_discover` — find emerging artists (no write)
    - `spotify_curator_generate_mood` — single-mood playlist
    - `spotify_curator_generate_weekly` — full 6-mood weekly digest
    - `spotify_curator_get_reports` — past run history
  - Mood taxonomy: cheerful, calming, stimulating, focus, melancholic, energetic
- ✅ **154/154 tests PASS** (24 new for hermes_skill tools)
- 📋 **FP-3b** MusicBrainz discovery (next)
- 📋 **FP-3c** Bandcamp scraping
- 📋 **FP-3d** Reddit community mining
- 📋 **FP-7** Integration tests with vcrpy cassettes
- 📋 **FP-9** Daemon mode + scheduled weekly digest

## 🚀 Quick start (Docker)

```bash
cd ~/projects/spotify-curator/deploy
cp .env.example .env
nano .env  # Fill in API keys

# Run OAuth on host Mac (one-time, browser required)
./scripts/auth_on_host.sh

# Build and start
docker compose build
docker compose up -d

# Talk to the containerized agent
docker compose exec hermes-spotify-curator hermes chat -q "show me this week's calming picks"
```

The container runs Hermes Agent with the spotify-curator skill preloaded.
It listens on port 8642 (Hermes API) and 8788 (future IPC).

## 📊 Test coverage

```
tests/test_api_v2.py            — 15/15 PASS
tests/test_profile.py           — 16/16 PASS
tests/test_lastfm.py            — 10/10 PASS
tests/test_listenbrainz.py      — 15/15 PASS
tests/test_ranking.py           — 38/38 PASS
tests/test_playlist_builder.py  — 19/19 PASS
tests/test_cli.py               — 17/17 PASS
tests/test_hermes_skill.py      — 24/24 PASS  ← NEW: Hermes tools
```

Total **154/154 PASS** in under 5 seconds.

## 🚀 Quick start

```bash
# 1. Install
git clone https://github.com/YOUR_USERNAME/spotify-curator.git
cd spotify-curator
python3 -m pip install -e ".[dev]"

# 2. Spotify credentials
#    Create app: https://developer.spotify.com/dashboard
cp .env.example .env
#    Edit .env: SPOTIPY_CLIENT_ID, SPOTIPY_CLIENT_SECRET, LASTFM_API_KEY

# 3. Authenticate
spotify-curator auth                    # Opens browser for OAuth

# 4. Build profile
spotify-curator profile build

# 5. Find emerging artists (preview)
spotify-curator discover lastfm --tag "indie rock" --period 6month

# 6. Build & write a playlist to Spotify
spotify-curator playlist create "Indie Rising" --tag "indie rock"

# Or just preview without writing
spotify-curator playlist create "Indie Rising" --dry-run
```

## 📊 Test coverage

```
tests/test_api_v2.py            — 15/15 PASS  (Spotify API v2 wrapper)
tests/test_profile.py           — 16/16 PASS  (FP-2: User profile builder)
tests/test_lastfm.py            — 10/10 PASS  (FP-3: Last.fm discovery)
tests/test_listenbrainz.py      — 15/15 PASS  (FP-3: ListenBrainz Labs)
tests/test_ranking.py           — 38/38 PASS  (FP-4: Ranking algorithm)
tests/test_playlist_builder.py  — 19/19 PASS  (FP-5: Playlist builder)
tests/test_cli.py               — 17/17 PASS  (FP-6: Typer CLI)
```

Total **130/130 PASS** in under 5 seconds.

## ⚠️ Critical: Spotify's Feb 2026 API changes

Spotify significantly changed the Web API on 2026-02-06
([migration guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide)).
The project takes this into account from day one.

### Removed fields (most important)
- ❌ `artist.popularity` (0-100) — no longer available in artist lookups
- ❌ `album.available_markets` — no country codes
- ❌ `album.album_group` — relationship between artist and album no longer returned
- ❌ Track-level `album_group`
- ❌ Genre data is being quietly deprecated

### Removed endpoints
- ❌ `GET /artists/{id}/top-tracks` — **artist's top tracks not available!**
- ❌ `GET /browse/new-releases` — new releases removed
- ❌ `GET /users/{id}/playlists` — user's playlists
- ❌ `GET /me/following` — followed artists
- ⚠️ Search `limit` reduced from 50→10

### New endpoints (used by the project)
- ✅ `PUT /me/library` — save to library
- ✅ `POST /playlists/{id}/items` — add items to playlist

### Impact on the project

**Spotipy 2.26 has NOT yet been updated** for the Feb 2026 changes.
We wrote our own `api_v2.py` wrapper that calls `sp._get/_post/_put/_delete`
directly with the new paths. Top tracks workaround: fetch the artist's
albums → the album's tracks → sort by album-popularity (still available
on album objects).

**The popularity field removal** is a bigger problem. Hybrid data from
multiple sources:

| Signal | Source | Use |
|---|---|---|
| Followers + time | Spotify | Growth rate = emerging |
| Album.popularity | Spotify (still!) | Helper signal |
| Last.fm scrobble growth | Last.fm API | Direct emerging signal |
| ListenBrainz ML-similar | ListenBrainz Labs | No Spotify popularity bubble |
| MusicBrainz genre | MusicBrainz API | Genre accuracy |
| Bandcamp followers-low | Web-scrape | Indie/small-label community |
| r/indieheads mentions | Reddit JSON | Human recommendations |

## 🏗️ Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 4C model (Context,
Container, Component, Code) with description of 6 data sources.

## 📋 FP plan

See [docs/FP_PLAN.md](docs/FP_PLAN.md) — WSJF prioritization, FP-2 details,
acceptance criteria.

## 🚀 Quick start

```bash
# 1. Clone
git clone https://github.com/YOUR_USERNAME/spotify-curator.git
cd spotify-curator

# 2. Install dependencies
python3 -m pip install -r requirements.txt

# 3. Spotify credentials
#    Create app: https://developer.spotify.com/dashboard
cp .env.example .env
#    Edit .env: SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET

# 4. Run tests
pytest tests/ -v

# 5. (Coming) OAuth login
# spotify-curator auth
```

## 📊 Test coverage

```
tests/test_api_v2.py            — 15/15 PASS  (Spotify API v2 wrapper)
tests/test_profile.py           — 16/16 PASS  (FP-2: User profile builder)
tests/test_lastfm.py            — 10/10 PASS  (FP-3: Last.fm discovery)
tests/test_listenbrainz.py      — 15/15 PASS  (FP-3: ListenBrainz Labs)
tests/test_ranking.py           — 38/38 PASS  (FP-4: Ranking algorithm)
tests/test_playlist_builder.py  — 19/19 PASS  (FP-5: Playlist builder)
```

Total **113/113 PASS** in under 5 seconds.

## 📚 Important links

- [Spotify Web API Feb 2026 Migration](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide)
- [Spotify Web API Reference](https://developer.spotify.com/documentation/web-api)
- [Last.fm API](https://www.last.fm/api)
- [MusicBrainz API](https://musicbrainz.org/doc/MusicBrainz_API)
- [ListenBrainz Labs](https://labs.api.listenbrainz.org/)
- [spotipy GitHub](https://github.com/spotipy-dev/spotipy)

## License

MIT — see [LICENSE](LICENSE).
