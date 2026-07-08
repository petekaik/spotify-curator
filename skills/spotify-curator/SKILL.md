---
name: spotify-curator
description: "Find emerging artists and build Spotify playlists using the local spotify-curator package. Use when the user wants to discover new music, build a mood-based playlist, refresh their taste profile, or check the status of their weekly digest."
version: 0.1.0
author: Pomo
license: MIT
metadata:
  hermes:
    tags: [spotify, music, discovery, playlist, mood, weekly]
    homepage: https://github.com/petekaik/spotify-curator
    related_skills: [hermes-agent]
---

# Spotify Curator

Build Spotify playlists of **emerging artists** matching the user's taste
profile. Discovers small/niche artists across multiple data sources
(Last.fm, ListenBrainz, MusicBrainz, Bandcamp, Reddit) and ranks them
by 6-component score (genre match + emerging signal + audio features +
discovery bonus + geo bonus − mainstream penalty).

## When to use this skill

Trigger on any of these user intents:

- "Build me a playlist" / "find new music" / "weekly digest"
- "What are some good [mood] tracks?" / "playlists for [genre]"
- "Refresh my profile" / "update my taste profile"
- "What's my top genre right now?" / "show my profile"
- "Are there good emerging artists in [tag]?"
- "Generate a [mood] playlist and write it to Spotify"

Do NOT trigger on:
- Playing a specific track the user already named (use Spotify directly)
- General music trivia (use web_search)
- Lyrics, biographies, album reviews (use web_search)

## How to invoke

This skill exposes **6 tools** (see references/commands.md for full schema).
When the user asks for any spotify-curator action, call the matching tool
and pass the result back conversationally. The tool returns a structured
JSON dict — present the most useful fields to the user, not the raw blob.

```python
# Example: user says "build me a calming playlist for this week"
result = await tool("spotify_curator_generate_mood", mood="calming", n_artists=20)
# result = {
#   "playlist_url": "https://open.spotify.com/playlist/abc",
#   "playlist_name": "Weekly Calming — 2026-W28",
#   "artists_count": 20,
#   "tracks_count": 40,
#   "artists": [...],
#   "summary": "20 emerging artists across ambient, post-rock, dream pop..."
# }
await tool("tts_speak", text=f"Built your weekly calming playlist. {result['summary']}")
```

## Critical environment requirements

- **`SPOTIPY_CLIENT_ID`** and **`SPOTIPY_CLIENT_SECRET`** in env
- **`SPOTIPY_CACHE_PATH`** pointing to a valid OAuth token cache
- **`LASTFM_API_KEY`** in env (for Last.fm discovery)
- Spotify OAuth must be completed on a host with a browser BEFORE this
  skill is usable in a headless context. See deploy/README.md.

If any of these are missing, the matching tool returns `{"error": "..."}`
with a clear message. Surface that to the user, do not retry blindly.

## What the tools do (short version)

| Tool | Purpose |
|---|---|
| `spotify_curator_status` | Auth + profile + last run info |
| `spotify_curator_refresh_profile` | Rebuild taste profile from Spotify |
| `spotify_curator_discover` | Find + rank emerging artists (no playlist write) |
| `spotify_curator_generate_weekly` | Full weekly digest across all moods |
| `spotify_curator_generate_mood` | Single-mood playlist (cheerful/calming/etc) |
| `spotify_curator_get_reports` | List past runs + their Spotify playlist URLs |

## When to fall back to direct CLI

The tools cover 90% of use cases. For one-off operations (building from
a custom tag, dry-run previews, tweaking ranking weights) use the CLI:

```bash
python -m src.cli.main playlist create "My Mix" --tag "shoegaze" --dry-run
python -m src.cli.main discover lastfm --tag "post-rock" --period 1month
```

These are the same commands available inside the Docker container at
`/opt/project/src/cli/main.py`.

## Architecture

```
┌─────────────────────────────────────────────────┐
│ Hermes Agent (this container)                   │
│  ┌──────────────────────────────────────────┐  │
│  │  spotify-curator skill (this file)       │  │
│  │  exposes 6 tools                         │  │
│  └────────────┬─────────────────────────────┘  │
│               │ tool calls                      │
│  ┌────────────▼─────────────────────────────┐  │
│  │  src/  (the package)                     │  │
│  │   - spotify/auth.py   (OAuth)            │  │
│  │   - spotify/api_v2.py (Feb 2026 wrapper) │  │
│  │   - analyzer/profile.py                  │  │
│  │   - discovery/sources/                   │  │
│  │     (lastfm, listenbrainz, musicbrainz,  │  │
│  │      bandcamp, reddit)                   │  │
│  │   - discovery/ranking.py (6-component)   │  │
│  │   - playlist/builder.py                  │  │
│  │   - cli/main.py (Typer)                  │  │
│  └──────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

See [references/architecture.md](references/architecture.md) for the
full 4C model.

## Cross-references

- [references/commands.md](references/commands.md) — full tool schemas
- [references/api.md](references/api.md) — IPC API (for future daemon mode)
- [references/mood_taxonomy.md](references/mood_taxonomy.md) — mood definitions
- [../../deploy/README.md](../../deploy/README.md) — Docker deployment
- [../../README.md](../../README.md) — package README
