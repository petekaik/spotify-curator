# Tool reference — spotify-curator

This document defines the 6 tools exposed by the spotify-curator skill.
Each tool returns a **JSON dict** that the agent can pass back to the user
or feed into another tool (e.g. `tts_speak`).

---

## `spotify_curator_status`

Check whether the user is authenticated, has a profile, and when the
last run happened.

**Input:** none

**Output:**
```json
{
  "authenticated": true,
  "user_id": "spotify-user-id",
  "profile_built": true,
  "profile_age_hours": 3.2,
  "lastfm_configured": true,
  "tools_healthy": true
}
```

**When the user asks:** "is everything set up?", "check the connection",
"is my profile fresh?"

---

## `spotify_curator_refresh_profile`

Rebuild the user's taste profile from Spotify listening history.
This is the **prerequisite** for any personalized playlist.

**Input:**
- `include_audio_features` (bool, default `true`) — fetch Spotify's
  audio features (tempo/energy/valence). Slower but improves ranking.
  Set to `false` if the deprecated endpoint is failing.

**Output:**
```json
{
  "success": true,
  "user_id": "spotify-user-id",
  "artists_count": 142,
  "genres_count": 23,
  "tracks_count": 150,
  "top_genres": ["indie rock", "dream pop", "shoegaze"],
  "top_artists": ["artist-id-1", "artist-id-2"],
  "duration_seconds": 28
}
```

**When the user asks:** "refresh my profile", "rebuild my taste profile",
"my profile is stale, update it"

**Warning:** This is destructive — overwrites the cached profile. The old
profile is not backed up.

---

## `spotify_curator_discover`

Find and rank emerging artists matching the user's profile, but **do not
write anything to Spotify**. Use this to preview candidates before committing.

**Input:**
- `tag` (str, default `"indie rock"`) — Last.fm tag to search
- `period` (str, default `"6month"`) — `"overall"`, `"7day"`, `"1month"`,
  `"3month"`, `"6month"`, `"12month"`
- `limit` (int, default `30`) — max candidates to return
- `use_listenbrainz` (bool, default `true`) — also use ListenBrainz
  ML-similar for cross-validation

**Output:**
```json
{
  "tag": "indie rock",
  "period": "6month",
  "candidates_count": 47,
  "ranked": [
    {
      "rank": 1,
      "name": "New Indie Artist",
      "score": 0.83,
      "lastfm_listeners": 5400,
      "tags": ["indie rock", "dream pop"],
      "score_breakdown": {
        "genre_match": 0.32,
        "emerging_signal": 0.21,
        "feature_match": 0.18,
        "discovery_bonus": 0.09,
        "mainstream_penalty": -0.05
      }
    },
    ...
  ]
}
```

**When the user asks:** "what are some good emerging artists in [tag]?",
"show me the top candidates", "preview before building a playlist"

---

## `spotify_curator_generate_weekly`

Full weekly digest. Generates playlists for **all 6 moods** and writes
each to Spotify as a separate playlist. Returns URLs to all of them.

**Input:**
- `n_artists_per_mood` (int, default `15`) — artists to rank per mood
- `tracks_per_artist` (int, default `2`) — 2-3 recommended
- `public` (bool, default `false`) — make playlists public
- `refresh_profile_first` (bool, default `false`) — if `true`, rebuild
  profile before generating (slower but fresher)

**Output:**
```json
{
  "success": true,
  "playlists": [
    {
      "mood": "cheerful",
      "url": "https://open.spotify.com/playlist/abc",
      "name": "Weekly Cheerful — 2026-W28",
      "artists_count": 15,
      "tracks_count": 30
    },
    {
      "mood": "calming",
      "url": "https://open.spotify.com/playlist/def",
      ...
    },
    ...
  ],
  "total_artists": 87,
  "total_tracks": 174,
  "duration_seconds": 142
}
```

**When the user asks:** "build my weekly digest", "generate this week's
playlists", "do the full run"

**Warning:** This is the heavy operation. 5-10 minutes typical. Multiple
playlists written to Spotify. **Profile must be fresh** — call
`spotify_curator_refresh_profile` first if it hasn't been built.

---

## `spotify_curator_generate_mood`

Single-mood playlist. Faster than the full weekly run.

**Input:**
- `mood` (str, **required**) — one of: `"cheerful"`, `"calming"`,
  `"stimulating"`, `"focus"`, `"melancholic"`, `"energetic"`
- `n_artists` (int, default `15`)
- `tracks_per_artist` (int, default `2`)
- `name` (str, optional) — override the auto-generated name
- `public` (bool, default `false`)

**Output:**
```json
{
  "success": true,
  "mood": "calming",
  "playlist_url": "https://open.spotify.com/playlist/xyz",
  "playlist_name": "Weekly Calming — 2026-W28",
  "artists_count": 15,
  "tracks_count": 30,
  "artists": ["New Artist 1", "New Artist 2", ...],
  "summary": "15 emerging artists spanning ambient, post-rock, and dream pop. Notable: ..."
}
```

**When the user asks:** "build a calming playlist", "give me something
for focus mode", "what's good for cheering me up?"

See [mood_taxonomy.md](mood_taxonomy.md) for what each mood means.

---

## `spotify_curator_get_reports`

List past runs and their Spotify playlist URLs. Useful for "what did you
build last week?" or "show me all my weekly digests".

**Input:**
- `limit` (int, default `10`) — most recent N runs
- `mood_filter` (str, optional) — only return runs containing this mood

**Output:**
```json
{
  "runs": [
    {
      "id": "2026-W28",
      "generated_at": "2026-07-08T22:00:00Z",
      "playlists": [
        {"mood": "cheerful", "url": "...", "tracks": 30},
        {"mood": "calming", "url": "...", "tracks": 30}
      ]
    },
    ...
  ]
}
```

**When the user asks:** "what did you build last week?", "show me my
recent digests", "find my [mood] playlist from [time]"

---

## Error responses

All tools return `{"error": "...", "details": "..."}` on failure rather
than raising exceptions. Common errors:

| Error | Cause | Fix |
|---|---|---|
| `not_authenticated` | No OAuth token | Run `deploy/scripts/auth_on_host.sh` |
| `profile_not_built` | No cached profile | Call `spotify_curator_refresh_profile` |
| `lastfm_not_configured` | Missing API key | Add `LASTFM_API_KEY` to `.env` |
| `spotify_api_error` | Spotify 4xx/5xx | Check `LASTFM_API_KEY` and network |
| `rate_limited` | Too many requests | Wait 1 minute, retry |

When you get an error, surface the message to the user verbatim. Don't
retry the same call in a loop — the user needs to fix the underlying issue.
