# Spotify Curator — 4C Architecture

**Date:** 2026-07-08
**Version:** v0.1.0
**Status:** FP-0/FP-1/FP-1b/FP-2 complete, FP-3 in progress

## 1. Context

### Problem

Spotify's own "Discover Weekly" and "Release Radar" lean heavily on
popularity, creating a feedback loop where already-popular artists get
more exposure. This project flips the logic: **the less heard, the more
interesting the discovery** — as long as it fits the user's taste profile.

### Constraints

- **Spotify Web API broke things in Feb 2026** — see README.md for full
  migration notes. Three things are now impossible via the standard API:
  1. `artist.popularity` (removed)
  2. `GET /artists/{id}/top-tracks` (removed)
  3. Search limit reduced from 50 → 10

- **Spotipy 2.26 hasn't caught up** — its high-level methods still
  target removed endpoints. Workaround: call `sp._get/_post/_put/_delete`
  directly with new paths. Our `api_v2.py` encapsulates this.

- **User account is single** — one intended user. We don't
  need multi-tenant support, just a clean personal CLI.

- **No web UI required for v1** — CLI is enough. Web UI is FP-8 backlog.

### User profile (sample listener)

- **Primary:** indie rock, post-rock, dream pop, shoegaze
- **Secondary:** electronic, ambient, IDM
- **Tertiary:** classical, jazz (when context-appropriate)
- **Explicit dislike:** mainstream pop, EDM, mumble rap, country

The system should err toward "more obscure" when uncertain.

### Environment

- **Host:** macOS 26.5.1 (Tahoe), Apple M1
- **Python:** 3.12.3 (system)
- **Libraries:** spotipy 2.26, pytest 9.1, pytest-mock 3.15
- **APIs:** Spotify Web API (auth + write), Last.fm API (discovery), MusicBrainz (genre fallback)
- **Storage:** Parquet via pyarrow, JSON fallback for small data
- **CLI:** Typer + Rich

---

## 2. Container

```
spotify-curator/
├── .github/
│   └── workflows/
│       └── ci.yml                    # pytest on push
├── docs/
│   ├── ARCHITECTURE.md               # this file
│   ├── FP_PLAN.md                    # roadmap
│   └── DECISIONS.md                  # ADRs
├── src/
│   ├── spotify/                      # API layer
│   │   ├── auth.py                   # OAuth flow, token cache
│   │   ├── api_v2.py                 # new endpoints (Feb 2026)
│   │   └── client.py                 # singleton accessor
│   ├── analyzer/                     # user profile
│   │   ├── profile.py                # genre + artist weighting
│   │   ├── features.py               # audio features extraction
│   │   └── cache.py                  # Parquet persistence
│   ├── discovery/                    # artist discovery
│   │   ├── sources/
│   │   │   ├── lastfm.py             # Last.fm tag-based search
│   │   │   ├── musicbrainz.py        # MusicBrainz genre data
│   │   │   └── similar_artists.py    # Last.fm similar-artist graph
│   │   ├── ranking.py                # score formula
│   │   └── filters.py                # dedup, blacklist
│   ├── playlist/                     # playlist construction
│   │   ├── builder.py                # main flow
│   │   ├── track_selector.py         # N tracks per artist
│   │   └── shuf.py                   # no-2-in-a-row shuf
│   ├── cli/                          # Typer entry point
│   │   ├── main.py
│   │   ├── auth.py
│   │   ├── profile.py
│   │   ├── discover.py
│   │   └── playlist.py
│   └── web/                          # FP-8 (backlog)
├── tests/                            # pytest + vcrpy for HTTP fixtures
│   ├── fixtures/
│   │   └── cassettes/                # recorded API responses
│   ├── test_auth.py
│   ├── test_api_v2.py                # ✅ 15/15 PASS
│   ├── test_profile.py
│   ├── test_ranking.py
│   ├── test_playlist_builder.py
│   └── test_cli.py
├── data/                             # gitignored
│   ├── cache/                        # Parquet/JSON
│   └── exports/                      # rendered playlists
├── .env.example
├── .gitignore
├── requirements.txt
├── pyproject.toml                    # build + tool config
├── README.md                         # user-facing
└── LICENSE                           # MIT
```

### Process model

- **One-shot CLI** — each command runs, does its thing, exits
- **Local state** — token cache, profile cache, ranking weights in `~/.spotify-curator/`
- **No long-running daemon** (yet) — FP-9 may add a daemon for daily refresh
- **No DB** — Parquet files are enough for v1's data scale

---

## 3. Components

### 3.1 Spotify auth (`src/spotify/auth.py`)

- **Responsibility:** OAuth Authorization Code Flow, token refresh
- **Input:** `SPOTIPY_CLIENT_ID`, `SPOTIPY_CLIENT_SECRET`, `SPOTIPY_REDIRECT_URI` from `.env`
- **Output:** authenticated `spotipy.Spotify` instance
- **State:** `~/.spotify-curator/.cache` (spotipy format)
- **Why custom wrapper:** spotipy works fine for auth; we just add
  - clear error messages when env vars missing
  - `is_authenticated()` check (no browser prompt)
  - `get_current_user_id()` helper

### 3.2 Spotify API v2 (`src/spotify/api_v2.py`)

- **Responsibility:** wrap Feb-2026 new endpoints that spotipy doesn't support
- **Endpoints:**
  - `me/library/contains` (GET) — replaces `/me/tracks/contains`
  - `me/library` (PUT/DELETE) — replaces `/me/tracks`, `/me/albums`, etc.
  - `playlists/{id}/items` (GET/POST/DELETE) — replaces `/playlists/{id}/tracks`
  - `artists/{id}/albums` + `albums/{id}/tracks` (workaround for removed `/artists/{id}/top-tracks`)
  - `search` (with cap at 10)
- **Test coverage:** 15/15 PASS, including regressions that fail if old paths are used

### 3.3 User profile (`src/analyzer/profile.py`) — **FP-2 complete**

- **Responsibility:** build a weighted taste profile from Spotify listening history
- **Inputs:**
  - `me/top/artists?time_range=short_term` (last 4 weeks)
  - `me/top/artists?time_range=medium_term` (last 6 months)
  - `me/top/artists?time_range=long_term` (all time)
  - `me/top/tracks?time_range=...` (same three windows)
- **Outputs:**
  ```python
  Profile:
    genre_weights: dict[str, float]    # normalized 0-1
    artist_weights: dict[str, float]   # Spotify artist_id → weight
    track_features_centroid: dict      # avg tempo, energy, valence, etc.
    saved_albums: list[dict]           # for "familiar territory" comparison
    updated_at: datetime
  ```
- **Weighting scheme:**
  ```
  short_term:  0.5   # recent habits matter most
  medium_term: 0.3   # stable preferences
  long_term:   0.2   # foundational taste
  ```
- **Genre aggregation:** use artist.genres (still available even with popularity
  removed; spotipy returns [] for many artists, so we'll cross-reference with
  Last.fm tags in FP-3)
- **Persistence:** Parquet file at `~/.spotify-curator/profile.parquet`

### 3.4 Audio features (`src/analyzer/features.py`)

- **Responsibility:** aggregate track-level audio features
- **Sources:**
  - Spotify's `audio-features` endpoint (DEPRECATED but still works as of 2026-07)
  - Fallback: Last.fm `track.getInfo` for some metadata
- **Output:** centroid of (tempo, energy, valence, danceability, acousticness)
- **Use:** "nouseva artisti" should match the user's overall feature profile

### 3.5 Discovery sources (`src/discovery/sources/`)

The core of "finding emerging artists" — Spotify can't do this alone anymore
(popularity is gone). We aggregate from **six complementary sources**, each
covering a different signal:

| Source | Signal | Endpoint | Why |
|---|---|---|---|
| **Last.fm** | Emerging by tag + scrobbles | `tag.getTopArtists?period=6month` | Real listening data, filters by recency |
| **Last.fm** | Similar artists | `artist.getSimilar` | "If you like X, try Y" |
| **MusicBrainz** | Genre + relations | `/ws/2/artist?query=...` | Best genre taxonomy, free, no auth |
| **ListenBrainz Labs** | ML-similar artists | `labs.api.listenbrainz.org/similar-artists` | Algorithm-discovered similarities, no Spotify bias |
| **Bandcamp** | Indie + small labels | tag pages, daily charts | Where indie/underground actually releases first |
| **r/indieheads** | Community discovery | Reddit JSON API | Real humans sharing, voting; reflects actual taste |

#### 3.5.1 Last.fm (`lastfm.py`) — primary

- `tag.getTopArtists(period='6month')` filtered by listener count < 50k
- `artist.getInfo` for scrobble counts and bio
- `artist.getSimilar(seed=...)` for similarity graph expansion
- Requires `LASTFM_API_KEY` in `.env`

#### 3.5.2 MusicBrainz (`musicbrainz.py`) — secondary

- `/ws/2/artist?query=tag:indie%20AND%20type:group` for genre-filtered search
- Excellent genre coverage, no auth required (just User-Agent)
- Rate limit: 1 request/sec (we respect this)
- python lib: `musicbrainzngs`

#### 3.5.3 ListenBrainz (`listenbrainz.py`) — NEW for FP-3

- Labs API: `https://labs.api.listenbrainz.org/similar-artists/<mbid_artist_list>`
- Returns ML-derived similar artists (no Spotify popularity bias)
- Requires no auth, no key
- Built by the MetaBrainz foundation (same people as MusicBrainz)

#### 3.5.4 Bandcamp (`bandcamp.py`) — NEW for FP-3

- No public API but tag-pages and `bandcamp.com/tag/<tag>/discover` are HTML-scrapable
- Specifically good for: post-rock, ambient, drone, experimental, vaporwave,
  bedroom-pop, lo-fi — niche genres this project targets
- Use `httpx` + `selectolax` (fast HTML parser) — **NOT** BeautifulSoup
  (too slow for scraping)
- Identify emerging artists: those with < 1000 followers on their page
- Rate limit: 1 req/2s (be polite)

#### 3.5.5 Reddit (`reddit.py`) — NEW for FP-3

- **No auth needed** for public JSON: append `.json` to any URL
- `r/indieheads` "Fresh Indie Rock" weekly threads → mining thread titles
- `r/postrock`, `r/shoegaze`, `r/dreampop` (relevant niche genres)
- `r/listentothis` for "obscure" filter
- `r/Bandcamp` for new releases
- Pattern: weekly "FRESH" threads are goldmines, just regex the artist names
  and resolve via MusicBrainz/Spotify

#### 3.5.6 Similar-artist graph (`similar_artists.py`)

- Seed with user's top-50 artists
- For each seed, get similar-artists from Last.fm AND ListenBrainz
- Combine with weighted overlap (artist in both = higher signal)
- Filter out artists already in user's top-list

### Cross-source ranking

When we find a candidate artist from one source, we **cross-reference** it
with the other sources to confirm:

```
artist_emerging_score = (
    lastfm_scrobble_growth      # how fast scrobbles grow week-over-week
  + listenbrainz_similarity    # 0-1 score from ML
  + bandcamp_followers_low     # low count = emerging
  + reddit_mentions_recent     # mentions in last 30 days
  + musicbrainz_genre_match    # genre overlap with profile
)
```

This **defensive cross-checking** filters out the Spotify popularity-bubble
artists that any single source might leak through.

### 3.6 Ranking (`src/discovery/ranking.py`) — FP-4

```
artist_score = (
    0.40 * genre_match_score      # cosine sim with profile.genre_weights
  + 0.25 * emerging_score         # Last.fm scrobble growth + album count
  + 0.20 * feature_match_score    # audio features centroid distance
  + 0.10 * discovery_potential    # fewest albums = newest
  + 0.05 * geo_bonus              # local/regional scene bonus
  - 0.30 * mainstream_penalty     # > 100k monthly listeners = too big
)
```

### 3.7 Playlist builder (`src/playlist/builder.py`) — FP-5

- **Algorithm:**
  1. Take top N=30 artists from ranking
  2. For each artist, get 2-3 tracks via `artist_top_tracks_via_albums`
  3. Shuffle with constraint: no 2 tracks from same artist in a row
  4. Create Spotify playlist via `POST /me/playlists` (the non-deprecated endpoint)
  5. Add tracks via `POST /playlists/{id}/items` (the new path)

### 3.8 CLI (`src/cli/main.py`) — FP-6

Typer commands:
```
spotify-curator auth                    # OAuth browser flow
spotify-curator profile                 # show cached profile
spotify-curator profile build           # rebuild from Spotify
spotify-curator discover --genre=...    # find candidates, ranked
spotify-curator playlist create "Title" # build + write to Spotify
spotify-curator playlist sync <id>      # update existing (FP-7+)
```

---

## 4. Code

### Key technical decisions

| Decision | Choice | Rationale |
|---|---|---|
| Language | Python 3.12 | Same ecosystem as Clairvoyant-Optics, mature ML/data libs |
| Spotify lib | spotipy 2.26 + custom v2 wrapper | spotipy hasn't updated; v2 wrapper fills gap |
| Discovery | Last.fm (primary) + MusicBrainz (secondary) | Spotify can't tell us "emerging" anymore |
| Persistence | Parquet | Fast, type-safe, schema-less, plays well with pandas |
| CLI | Typer + Rich | Modern, type-safe, great defaults |
| Test framework | pytest + vcrpy | vcrpy records API calls for deterministic tests |
| License | MIT | FOSS |

### Concurrency model

- **v1:** synchronous, sequential API calls
- **Batch size:** max 50 IDs per call (Spotify library limit)
- **Rate limits:** spotipy has built-in rate-limit handling; we don't
  override it
- **Caching:** profile is rebuilt on demand; discovery results cached
  in Parquet with TTL=7 days

### Error handling

- **Auth errors:** fail fast with clear message + setup instructions
- **API errors:** retry 3x with exponential backoff for 5xx; surface 4xx
- **Missing data:** graceful degradation (e.g., no audio features → use
  zero-vector; no genres → use empty list)
- **Network errors:** cache last good result; warn on stale

### Performance budget

- `auth`: < 5s (browser flow) or < 100ms (cached)
- `profile build`: < 30s (5 API calls, max 50 artists each)
- `discover`: < 60s (Last.fm + MusicBrainz + ranking)
- `playlist create`: < 45s (30 artists × 3 tracks + add to Spotify)
- Total end-to-end: < 3 min for full run

### Security

- `.env` is gitignored; never commit
- OAuth tokens cached with file mode 0600
- No logging of tokens or PII
- No telemetry

### Dependencies

```
# Core
spotipy>=2.25.0
requests>=2.31.0
python-dotenv>=1.0.0

# Data
pandas>=2.2.0
pyarrow>=18.0.0

# CLI
typer>=0.12.0
rich>=13.7.0

# Last.fm (no official Python lib, use requests)
# MusicBrainz: musicbrainzngs (optional)

# Testing
pytest>=8.3.0
pytest-asyncio>=0.24.0
pytest-mock>=3.14.0
vcrpy>=6.0.0
```

### What we explicitly don't do (v1)

- ❌ Real-time listening tracking (Spotify deprecated recent-played for non-Premium)
- ❌ Multi-user support
- ❌ Web UI (FP-8 backlog)
- ❌ Daemon mode (FP-9 backlog)
- ❌ Local LLM for ranking (FP-4 uses hand-tuned formula)
- ❌ Playlist auto-refresh on schedule (FP-9+)

---

## Cross-references

- [README.md](../README.md) — user-facing, FP status, Spotify Feb-2026 notes
- [FP_PLAN.md](FP_PLAN.md) — detailed backlog with WSJF scores
- [Clairvoyant-Optics ARCHITECTURE.md](../../Clairvoyant-Optics/docs/ARCHITECTURE.md) — sibling project reference
