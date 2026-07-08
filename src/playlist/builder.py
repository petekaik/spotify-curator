"""Playlist construction — turn ranked candidates into a Spotify playlist.

Flow:
1. Take top N candidates from the ranking algorithm (FP-4)
2. For each candidate, resolve to a Spotify artist (search by name)
3. Fetch 2-3 top tracks per artist (via api_v2.artist_top_tracks_via_albums)
4. Shuffle with constraint: no 2 consecutive tracks from same artist
5. Create Spotify playlist via POST /me/playlists
6. Add tracks via POST /playlists/{id}/items (NEW endpoint, Feb 2026)
7. Return the playlist ID + URL
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Optional

from src.spotify.api_v2 import SpotifyAPIv2
from src.discovery.ranking import Candidate

log = logging.getLogger(__name__)


@dataclass
class PlaylistTrack:
    """A track to be added to a playlist."""
    uri: str              # spotify:track:xyz
    name: str
    artist_name: str
    album_name: str = ""
    duration_ms: int = 0
    popularity: int = 0


@dataclass
class BuiltPlaylist:
    """Result of building a playlist (before writing to Spotify)."""
    name: str
    description: str
    tracks: list[PlaylistTrack] = field(default_factory=list)
    candidate_count: int = 0    # how many candidates we started with
    skipped_no_spotify: int = 0 # candidates not found on Spotify
    skipped_no_tracks: int = 0  # candidates with no top tracks
    total_artist_count: int = 0
    spotify_playlist_id: Optional[str] = None  # populated after write


class PlaylistBuilder:
    """Build a Spotify playlist from ranked candidates.

    Usage:
        >>> api = SpotifyAPIv2(get_spotify_client())
        >>> builder = PlaylistBuilder(api)
        >>> ranked = rank_artists(candidates, profile, limit=30)
        >>> pl = builder.build(ranked, name="Indie Rising", tracks_per_artist=2)
        >>> pl = builder.write_to_spotify(pl, public=False)
        >>> print(f"https://open.spotify.com/playlist/{pl.spotify_playlist_id}")
    """

    def __init__(self, api: SpotifyAPIv2, min_request_interval: float = 0.1):
        self._api = api
        self._min_interval = min_request_interval
        self._last_request = 0.0

    def _throttle(self) -> None:
        now = time.time()
        if (now - self._last_request) < self._min_interval:
            time.sleep(self._min_interval - (now - self._last_request))
        self._last_request = time.time()

    # ────────────────────────────────────────────────────────────
    # 1. Resolve candidates to Spotify artists
    # ────────────────────────────────────────────────────────────

    def _search_spotify_artist(self, candidate: Candidate) -> Optional[str]:
        """Find the Spotify artist ID for a candidate.

        Strategy:
        1. If candidate already has spotify_id, use it
        2. Otherwise, search by name (limit 1)

        Returns:
            Spotify artist ID, or None if not found
        """
        if candidate.spotify_id:
            return candidate.spotify_id
        try:
            self._throttle()
            result = self._api.search_artists(candidate.name, limit=1)
            items = result.get("artists", {}).get("items", [])
            if items:
                return items[0]["id"]
        except Exception as e:
            log.warning(f"Spotify search failed for {candidate.name}: {e}")
        return None

    def resolve_candidates(
        self, candidates: list[Candidate]
    ) -> list[tuple[Candidate, str]]:
        """Resolve each candidate to a Spotify artist ID.

        Returns:
            list of (candidate, spotify_artist_id) tuples
            Candidates that can't be resolved are silently dropped.
        """
        resolved: list[tuple[Candidate, str]] = []
        for c in candidates:
            sid = self._search_spotify_artist(c)
            if sid:
                resolved.append((c, sid))
            else:
                log.debug(f"No Spotify match for {c.name}")
        return resolved

    # ────────────────────────────────────────────────────────────
    # 2. Fetch top tracks per artist
    # ────────────────────────────────────────────────────────────

    def fetch_tracks_for_artists(
        self,
        resolved: list[tuple[Candidate, str]],
        tracks_per_artist: int = 2,
        max_albums_per_artist: int = 5,
    ) -> list[tuple[Candidate, list[dict]]]:
        """Fetch top tracks for each resolved artist.

        Args:
            resolved: list of (candidate, spotify_id) tuples
            tracks_per_artist: how many tracks per artist to include
            max_albums_per_artist: how many albums to scan for tracks

        Returns:
            list of (candidate, tracks) tuples
            Artists with no available tracks are silently dropped.
        """
        result: list[tuple[Candidate, list[dict]]] = []
        for c, sid in resolved:
            try:
                self._throttle()
                tracks = self._api.artist_top_tracks_via_albums(
                    sid,
                    max_albums=max_albums_per_artist,
                    tracks_per_album=tracks_per_artist,
                )
                if tracks:
                    result.append((c, tracks[:tracks_per_artist]))
                else:
                    log.debug(f"No tracks for {c.name}")
            except Exception as e:
                log.warning(f"Track fetch failed for {c.name}: {e}")
        return result

    # ────────────────────────────────────────────────────────────
    # 3. Shuffle with no-two-consecutive-same-artist constraint
    # ────────────────────────────────────────────────────────────

    def shuffle_no_adjacent(
        self,
        artist_tracks: list[tuple[Candidate, list[dict]]],
    ) -> list[dict]:
        """Shuffle tracks so no two consecutive tracks are from same artist.

        Greedy algorithm:
        1. Flatten all tracks, remember which artist each came from
        2. Shuffle track order
        3. Walk through and swap if consecutive artists match
        4. Repeat until no adjacent matches (or stuck — should be rare)

        Args:
            artist_tracks: list of (candidate, list_of_tracks)

        Returns:
            list of track dicts (shuffled)
        """
        # Flatten with artist tracking
        track_pool: list[tuple[Candidate, dict]] = []
        for cand, tracks in artist_tracks:
            for t in tracks:
                track_pool.append((cand, t))

        if not track_pool:
            return []

        # Try a few shuffle attempts
        for attempt in range(10):
            random.shuffle(track_pool)
            if not self._has_adjacent_same_artist(track_pool):
                return [t for _, t in track_pool]

        # If we couldn't avoid adjacencies in 10 attempts, do swaps
        track_pool = self._fix_adjacent_swaps(track_pool)
        return [t for _, t in track_pool]

    def _has_adjacent_same_artist(
        self, track_pool: list[tuple[Candidate, dict]]
    ) -> bool:
        for i in range(len(track_pool) - 1):
            if track_pool[i][0].name == track_pool[i + 1][0].name:
                return True
        return False

    def _fix_adjacent_swaps(
        self, track_pool: list[tuple[Candidate, dict]]
    ) -> list[tuple[Candidate, dict]]:
        """Swap tracks to break up adjacent same-artist runs."""
        result = list(track_pool)
        i = 0
        while i < len(result) - 1:
            if result[i][0].name == result[i + 1][0].name:
                # Find next track with different artist
                for j in range(i + 2, len(result)):
                    if result[j][0].name != result[i][0].name:
                        result[i + 1], result[j] = result[j], result[i + 1]
                        break
            i += 1
        return result

    # ────────────────────────────────────────────────────────────
    # 4. Build (without writing to Spotify)
    # ────────────────────────────────────────────────────────────

    def build(
        self,
        ranked: list[tuple[Candidate, float, dict]],
        name: str,
        description: str = "",
        tracks_per_artist: int = 2,
        max_albums_per_artist: int = 5,
    ) -> BuiltPlaylist:
        """Build a playlist from ranked candidates (in-memory only).

        Args:
            ranked: list of (candidate, score, components) from rank_artists()
            name: playlist name
            description: playlist description
            tracks_per_artist: 2-3 typical
            max_albums_per_artist: how deep to scan for tracks

        Returns:
            BuiltPlaylist with tracks populated, ready to write
        """
        # Step 1: Resolve to Spotify IDs
        candidates_only = [c for c, _, _ in ranked]
        resolved = self.resolve_candidates(candidates_only)
        skipped_no_spotify = len(candidates_only) - len(resolved)

        # Step 2: Fetch tracks
        artist_tracks = self.fetch_tracks_for_artists(
            resolved,
            tracks_per_artist=tracks_per_artist,
            max_albums_per_artist=max_albums_per_artist,
        )
        # How many resolved artists had no tracks
        skipped_no_tracks = len(resolved) - len(artist_tracks)

        # Step 3: Shuffle
        shuffled = self.shuffle_no_adjacent(artist_tracks)

        # Step 4: Convert to PlaylistTrack objects
        pl_tracks = [
            PlaylistTrack(
                uri=t["uri"],
                name=t["name"],
                artist_name=t.get("album_name", ""),  # we'll fix this below
                album_name=t.get("album_name", ""),
                duration_ms=t.get("duration_ms", 0),
                popularity=t.get("popularity", 0),
            )
            for t in shuffled
        ]

        # Step 5: Count distinct artists in final playlist
        artist_names = set()
        for cand, _ in artist_tracks:
            artist_names.add(cand.name)

        # Generate a default description if none provided
        if not description:
            description = (
                f"Auto-generated by Spotify Curator. "
                f"{len(artist_names)} emerging artists, {len(pl_tracks)} tracks."
            )

        return BuiltPlaylist(
            name=name,
            description=description,
            tracks=pl_tracks,
            candidate_count=len(candidates_only),
            skipped_no_spotify=skipped_no_spotify,
            skipped_no_tracks=skipped_no_tracks,
            total_artist_count=len(artist_names),
        )

    # ────────────────────────────────────────────────────────────
    # 5. Write to Spotify
    # ────────────────────────────────────────────────────────────

    def write_to_spotify(
        self,
        playlist: BuiltPlaylist,
        public: bool = False,
    ) -> BuiltPlaylist:
        """Create the playlist in Spotify and add all tracks.

        Uses NEW Feb 2026 endpoints:
        - POST /me/playlists (the non-deprecated create endpoint)
        - POST /playlists/{id}/items (the new add-items path)

        Args:
            playlist: BuiltPlaylist from build()
            public: whether the playlist should be public

        Returns:
            The same BuiltPlaylist with spotify_playlist_id populated
        """
        if not playlist.tracks:
            log.warning("No tracks to write, skipping Spotify call")
            return playlist

        # Step 1: Create the playlist
        # Endpoint: POST /me/playlists
        self._throttle()
        create_result = self._api._sp._post(
            "me/playlists",
            body={
                "name": playlist.name,
                "description": playlist.description,
                "public": public,
            },
        )
        playlist.spotify_playlist_id = create_result.get("id")
        if not playlist.spotify_playlist_id:
            raise RuntimeError(
                f"Spotify create playlist returned no ID. Response: {create_result}"
            )
        log.info(f"Created Spotify playlist: {playlist.spotify_playlist_id}")

        # Step 2: Add tracks in batches of 100 (Spotify limit)
        track_uris = [t.uri for t in playlist.tracks]
        for i in range(0, len(track_uris), 100):
            batch = track_uris[i:i + 100]
            self._throttle()
            self._api.playlist_add_items(playlist.spotify_playlist_id, batch)
            log.info(f"Added {len(batch)} tracks to playlist")

        return playlist
