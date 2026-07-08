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
as long as it fits the user's genre profile.

## 🧩 Features (FP-2 complete)

- ✅ **OAuth authentication** — Authorization Code Flow, token cache
- ✅ **Spotify API v2 wrapper** — handles Feb 2026 changes
  (`/me/library`, `/playlists/{id}/items`, workaround for removed
  `/artists/{id}/top-tracks`)
- ✅ **User profile builder** — top artists + top tracks across three
  time windows (short/medium/long term), weighted genre and artist
  analysis, Parquet cache
- ✅ **31/31 tests PASS** (pytest)
- 🚧 **Discovery modules** (FP-3) — Last.fm, ListenBrainz, MusicBrainz, Bandcamp, Reddit
- 📋 **Ranking algorithm** (FP-4) — genre + emerging + features + mainstream penalty
- 📋 **Playlist builder** (FP-5) — N best artists, 2-3 tracks per artist
- 📋 **Typer CLI** (FP-6)
- 📋 **Web UI** (FP-8, backlog)

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
tests/test_api_v2.py    — 15/15 PASS  (Spotify API v2 wrapper)
tests/test_profile.py   — 16/16 PASS  (FP-2: User profile builder)
```

Total **31/31 PASS** in under a second.

## 📚 Important links

- [Spotify Web API Feb 2026 Migration](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide)
- [Spotify Web API Reference](https://developer.spotify.com/documentation/web-api)
- [Last.fm API](https://www.last.fm/api)
- [MusicBrainz API](https://musicbrainz.org/doc/MusicBrainz_API)
- [ListenBrainz Labs](https://labs.api.listenbrainz.org/)
- [spotipy GitHub](https://github.com/spotipy-dev/spotipy)

## License

MIT — see [LICENSE](LICENSE).
