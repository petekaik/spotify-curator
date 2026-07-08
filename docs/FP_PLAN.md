# FP Plan — Feature Plan Backlog

**Last updated:** 2026-07-08
**Project:** Spotify Curator
**Version target:** v0.1.0 (FP-0 → FP-7) by end of July 2026

## Scoring system

```
WSJF = Business Value / Time-to-Complete
```

Where BV (Business Value) and TC (Time-to-Complete) are Fibonacci 1–13.
Just like Clairvoyant-Optics project convention.

---

## Current Sprint

| ID | Title | Area | BV | TC | WSJF | Status |
|---|---|---|---|---|---|---|
| **FP-0** | Project scaffold (dirs, README, docs) | Infra | 8 | 1 | 8.00 | ✅ Done |
| **FP-1** | OAuth auth + token cache | Spotify | 13 | 2 | 6.50 | ✅ Done |
| **FP-1b** | API v2 wrapper (Feb 2026 endpoints) | Spotify | 13 | 3 | 4.33 | ✅ Done, 15/15 PASS |
| **FP-2** | User profile builder | Analyzer | 13 | 3 | 4.33 | ✅ Done, 16/16 PASS |
| **FP-3** | Discovery: Last.fm + ListenBrainz | Discovery | 8 | 5 | 1.60 | ✅ Done, 25/25 PASS |
| **FP-4** | Ranking algorithm | Discovery | 13 | 5 | 2.60 | Open |

## Backlog (priority order)

| ID | Title | Area | BV | TC | WSJF |
|---|---|---|---|---|---|
| **FP-3b** | Discovery: MusicBrainz | Discovery | 5 | 3 | 1.67 |
| **FP-3c** | Discovery: Bandcamp scraping | Discovery | 8 | 5 | 1.60 |
| **FP-3d** | Discovery: Reddit community mining | Discovery | 8 | 8 | 1.00 |
| **FP-4** | Ranking algorithm | Discovery | 13 | 5 | 2.60 |
| **FP-5** | Playlist builder (write to Spotify) | Playlist | 13 | 3 | 4.33 | ✅ Done, 19/19 PASS |
| **FP-6** | Typer CLI | CLI | 8 | 2 | 4.00 | Open |
| **FP-7** | Integration tests with vcrpy cassettes | Test | 8 | 3 | 2.67 |
| **FP-8** | Web UI (FastAPI, lightweight) | UI | 5 | 5 | 1.00 |
| **FP-9** | Daemon mode (daily refresh) | Infra | 5 | 8 | 0.63 |
| **FP-10** | GH Actions CI | Infra | 5 | 2 | 2.50 |
| **FP-11** | First-run tutorial / config wizard | UX | 3 | 2 | 1.50 |

---

## FP-2: User Profile Builder (in progress)

**Goal:** Build a weighted user taste profile from Spotify listening history.

### Inputs

- `GET /me/top/artists?time_range=short_term` (last 4 weeks, limit 50)
- `GET /me/top/artists?time_range=medium_term` (last 6 months, limit 50)
- `GET /me/top/artists?time_range=long_term` (all time, limit 50)
- `GET /me/top/tracks?time_range=short_term` (last 4 weeks, limit 50)
- `GET /me/top/tracks?time_range=medium_term`
- `GET /me/top/tracks?time_range=long_term`
- `GET /me/library` (saved albums, NEW endpoint)

### Outputs

Parquet file at `~/.spotify-curator/profile.parquet`:

```python
@dataclass
class UserProfile:
    user_id: str
    updated_at: datetime
    genre_weights: dict[str, float]      # normalized 0-1
    artist_weights: dict[str, float]     # artist_id → weight
    top_artists_short: list[ArtistRef]   # 50 entries
    top_artists_medium: list[ArtistRef]
    top_artists_long: list[ArtistRef]
    top_tracks_short: list[TrackRef]
    top_tracks_medium: list[TrackRef]
    top_tracks_long: list[TrackRef]
    saved_album_ids: list[str]
    track_features_centroid: dict        # avg tempo, energy, valence, etc.
```

### Weighting scheme

```
short_term:  0.5   # recent habits (most important)
medium_term: 0.3   # stable preferences
long_term:   0.2   # foundational taste
```

### Sub-tasks

1. **Sub-task 2.1:** `src/analyzer/types.py` — dataclasses for Profile, ArtistRef, TrackRef
2. **Sub-task 2.2:** `src/analyzer/fetch.py` — fetch all top-* and library data via api_v2
3. **Sub-task 2.3:** `src/analyzer/weights.py` — compute genre + artist weights with time-range blending
4. **Sub-task 2.4:** `src/analyzer/cache.py` — Parquet read/write
5. **Sub-task 2.5:** `src/analyzer/profile.py` — main ProfileBuilder class
6. **Sub-task 2.6:** `tests/test_profile.py` — unit tests with mocked spotipy
7. **Sub-task 2.7:** Integration test with vcrpy cassette (real Spotify response)

### Acceptance criteria

- [ ] `from src.analyzer.profile import ProfileBuilder; builder = ProfileBuilder(sp); profile = builder.build(); profile.genre_weights` returns dict
- [ ] `profile.artist_weights` sums to ~1.0
- [ ] All 6 Spotify API calls wrapped with proper error handling
- [ ] Library data uses NEW `me/library` endpoint (regression test included)
- [ ] Profile persists to Parquet and re-loads correctly
- [ ] `tests/test_profile.py`: 10+ tests, all PASS
- [ ] CLI: `spotify-curator profile build` runs end-to-end (requires auth)

### Test data

- **Unit tests:** mock spotipy with synthetic top-artist responses
- **Integration test:** vcrpy cassette with captured real responses (anonymized)

---

## FP-3: Discovery — Last.fm + ListenBrainz

**Goal:** Find emerging artists matching user profile.

### Last.fm (primary)

- `tag.getTopArtists(tag='indie rock', period='6month')` — top by recent scrobbles
- Filter: listeners < 50,000 (emerging signal)
- Cross-reference: `artist.getInfo` for full bio + scrobble count
- Similarity: `artist.getSimilar(seed=user_top_artist)` for graph expansion

### ListenBrainz (primary)

- `POST https://labs.api.listenbrainz.org/similar-artists/` with list of MBIDs
- ML-derived scores (no Spotify popularity bias)
- Requires user to have ListenBrainz account (optional, can be skipped)
- Fallback: use just Last.fm if ListenBrainz unavailable

### Sub-tasks

1. `src/discovery/sources/lastfm.py` — Last.fm client (requires API key in .env)
2. `src/discovery/sources/listenbrainz.py` — ListenBrainz Labs client
3. `src/discovery/sources/__init__.py` — unified interface
4. `tests/test_lastfm.py` — unit tests with vcrpy
5. `tests/test_listenbrainz.py` — unit tests with vcrpy

### Acceptance criteria

- [ ] `LastFmClient().get_top_artists_by_tag('indie rock', period='6month')` returns list
- [ ] `ListenBrainzClient().get_similar_artists([mbid1, mbid2])` returns scored list
- [ ] Both respect rate limits
- [ ] Both handle 4xx/5xx with retry
- [ ] Both have vcrpy cassettes for offline tests

---

## FP-3b: MusicBrainz (fallback/secondary)

- `musicbrainzngs.search_artists(query='tag:indie AND type:group')`
- Good genre data, slow (1 req/sec limit)
- Use for: enriching artist metadata, genre taxonomy

## FP-3c: Bandcamp scraping

- Scrape `bandcamp.com/tag/<tag>` pages
- Use `httpx` + `selectolax`
- Specifically for post-rock, ambient, experimental
- Rate-limited to 1 req / 2s

## FP-3d: Reddit community mining

- Append `.json` to thread URLs (no auth)
- Weekly "FRESH" threads are goldmines
- Regex artist names → resolve via MusicBrainz

---

## FP-4: Ranking algorithm

**Inputs:** candidate artists from any discovery source
**Output:** ranked list of artists with scores

```
artist_score = (
    0.40 * genre_match_score      # cosine sim with profile.genre_weights
  + 0.25 * emerging_score         # cross-source emerging signals
  + 0.20 * feature_match_score    # audio features centroid distance
  + 0.10 * discovery_potential    # fewest albums = newest
  + 0.05 * geo_bonus              # local scene (configurable)
  - 0.30 * mainstream_penalty     # > 100k monthly listeners = too big
)
```

Tunable via `~/.spotify-curator/config.yaml`.

---

## FP-5: Playlist builder

**Algorithm:**

1. Take top N=30 from ranking
2. For each artist, get 2-3 tracks via `api_v2.artist_top_tracks_via_albums`
3. Shuffle with constraint: no 2 tracks from same artist in a row
4. Create playlist via `POST /me/playlists`
5. Add tracks via `POST /playlists/{id}/items` (NEW path)

---

## FP-6: Typer CLI

```
spotify-curator auth                 # OAuth browser flow
spotify-curator profile              # show cached profile (table)
spotify-curator profile build        # rebuild from Spotify
spotify-curator discover --genre=... # find candidates, ranked
spotify-curator playlist create "Title"  # build + write to Spotify
```

---

## Out of scope (v0.1.0)

- ❌ Real-time listening tracking
- ❌ Multi-user support
- ❌ Web UI (FP-8)
- ❌ Daemon mode (FP-9)
- ❌ Auto-schedule (cron-driven, FP-9+)

---

## Cross-references

- [README.md](../README.md) — user-facing
- [ARCHITECTURE.md](ARCHITECTURE.md) — 4C architecture
- [DECISIONS.md](DECISIONS.md) — Architecture Decision Records
