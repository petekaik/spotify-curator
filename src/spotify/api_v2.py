"""Low-level Spotify Web API wrapper for the new (Feb 2026) endpoints.

This module provides direct access to the endpoints that spotipy 2.26 doesn't
yet support. Uses spotipy's internal _get/_post helpers which handle
authentication and token refresh.

Key endpoints this wraps:
- POST /me/library       (replaces PUT /me/tracks etc.)
- GET  /playlists/{id}/items (replaces /playlists/{id}/tracks)
- POST /playlists/{id}/items (replaces /playlists/{id}/tracks)
- Album-based top tracks (since /artists/{id}/top-tracks was removed)
"""
from typing import Any, Optional
import spotipy


class SpotifyAPIv2:
    """Wrapper for the new (Feb 2026) Spotify Web API endpoints."""

    def __init__(self, sp: spotipy.Spotify):
        """Wrap an existing authenticated spotipy client.

        Args:
            sp: authenticated spotipy.Spotify instance
        """
        self._sp = sp

    # ───────────────────────────────────────────────────────────
    # LIBRARY (NEW - replaces /me/tracks, /me/albums, etc.)
    # ───────────────────────────────────────────────────────────

    def library_contains(self, item_type: str, item_ids: list[str]) -> list[bool]:
        """Check if items are saved in user's library.

        Args:
            item_type: "track", "album", "artist", "show", "episode", "audiobook"
            item_ids: list of Spotify IDs (max 50)

        Returns:
            list of bools matching item_ids order
        """
        if len(item_ids) > 50:
            raise ValueError("max 50 IDs per call")
        # Endpoint: GET /me/library/contains
        result = self._sp._get(
            "me/library/contains",
            type=item_type,
            ids=",".join(item_ids),
        )
        return result

    def library_save(self, item_type: str, item_ids: list[str]) -> None:
        """Save items to user's library.

        Args:
            item_type: "track", "album", "artist", "show", "episode", "audiobook"
            item_ids: list of Spotify IDs (max 50)
        """
        if len(item_ids) > 50:
            raise ValueError("max 50 IDs per call")
        # Endpoint: PUT /me/library
        self._sp._put(
            "me/library",
            body={"ids": item_ids, "type": item_type},
        )

    def library_remove(self, item_type: str, item_ids: list[str]) -> None:
        """Remove items from user's library.

        Args:
            item_type: "track", "album", etc.
            item_ids: list of Spotify IDs
        """
        if len(item_ids) > 50:
            raise ValueError("max 50 IDs per call")
        # Endpoint: DELETE /me/library
        self._sp._delete(
            "me/library",
            body={"ids": item_ids, "type": item_type},
        )

    # ───────────────────────────────────────────────────────────
    # PLAYLISTS (NEW paths - replaces /playlists/{id}/tracks)
    # ───────────────────────────────────────────────────────────

    def playlist_items(
        self,
        playlist_id: str,
        limit: int = 100,
        offset: int = 0,
        fields: Optional[str] = None,
    ) -> dict[str, Any]:
        """Get items in a playlist (NEW endpoint).

        Args:
            playlist_id: Spotify playlist ID
            limit: max 100
            offset: pagination
            fields: optional field filter (Spotify fields syntax)

        Returns:
            dict with 'items', 'total', 'limit', 'offset', 'next'
        """
        params = {"limit": min(limit, 100), "offset": offset}
        if fields:
            params["fields"] = fields
        # Endpoint: GET /playlists/{id}/items
        return self._sp._get(f"playlists/{playlist_id}/items", **params)

    def playlist_add_items(self, playlist_id: str, item_uris: list[str]) -> dict:
        """Add items to a playlist (NEW endpoint).

        Args:
            playlist_id: Spotify playlist ID
            item_uris: list of Spotify URIs (e.g. "spotify:track:xxx")

        Returns:
            dict with 'snapshot_id' for tracking the change
        """
        if len(item_uris) > 100:
            raise ValueError("max 100 URIs per call (paginate manually)")
        # Endpoint: POST /playlists/{id}/items
        return self._sp._post(
            f"playlists/{playlist_id}/items",
            body={"uris": item_uris, "position": 0},
        )

    def playlist_remove_items(self, playlist_id: str, item_uris: list[str]) -> dict:
        """Remove items from a playlist (NEW endpoint).

        Args:
            playlist_id: Spotify playlist ID
            item_uris: list of Spotify URIs
        """
        # Endpoint: DELETE /playlists/{id}/items
        return self._sp._delete(
            f"playlists/{playlist_id}/items",
            body={"uris": item_uris},
        )

    # ───────────────────────────────────────────────────────────
    # ARTIST TRACKS (since /artists/{id}/top-tracks was removed)
    # ───────────────────────────────────────────────────────────

    def artist_top_tracks_via_albums(
        self,
        artist_id: str,
        max_albums: int = 10,
        tracks_per_album: int = 5,
    ) -> list[dict]:
        """Get an artist's top tracks by fetching their albums and sorting.

        Workaround for the removed /artists/{id}/top-tracks endpoint.
        We fetch the artist's albums, get tracks from each, and return
        a deduplicated list sorted by album popularity (still available).

        Args:
            artist_id: Spotify artist ID
            max_albums: how many of the artist's albums to fetch
            tracks_per_album: max tracks to return per album

        Returns:
            list of track dicts, each with:
                - id, name, uri, popularity (track-level still available)
                - album_name, album_popularity, album_release_date
        """
        # Step 1: Get artist's albums
        albums_response = self._sp._get(
            f"artists/{artist_id}/albums",
            limit=min(max_albums, 50),  # max 50 in album-list
            include_groups="album,single",
        )
        albums = albums_response.get("items", [])

        # Sort albums by popularity (still available on album object)
        albums.sort(key=lambda a: a.get("popularity", 0), reverse=True)

        # Step 2: Get tracks from each album
        seen_track_ids = set()
        all_tracks = []

        for album in albums[:max_albums]:
            tracks_response = self._sp._get(
                f"albums/{album['id']}/tracks",
                limit=min(tracks_per_album, 50),
            )
            for track in tracks_response.get("items", []):
                if track["id"] in seen_track_ids:
                    continue
                seen_track_ids.add(track["id"])
                # Enrich with album metadata
                all_tracks.append({
                    "id": track["id"],
                    "name": track["name"],
                    "uri": track["uri"],
                    "popularity": track.get("popularity", 0),  # track-level
                    "duration_ms": track.get("duration_ms", 0),
                    "explicit": track.get("explicit", False),
                    "preview_url": track.get("preview_url"),
                    "album_name": album["name"],
                    "album_popularity": album.get("popularity", 0),
                    "album_release_date": album.get("release_date", ""),
                    "album_id": album["id"],
                })

        # Sort by album_popularity then track popularity
        all_tracks.sort(
            key=lambda t: (t["album_popularity"], t["popularity"]),
            reverse=True,
        )
        return all_tracks

    def artist_albums(self, artist_id: str, limit: int = 50) -> list[dict]:
        """Get all albums by an artist (with popularity still available).

        Args:
            artist_id: Spotify artist ID
            limit: max 50 (API max)

        Returns:
            list of album dicts with id, name, popularity, release_date
        """
        result = self._sp._get(
            f"artists/{artist_id}/albums",
            limit=min(limit, 50),
            include_groups="album,single,compilation",
        )
        return result.get("items", [])

    def artist_info(self, artist_id: str) -> dict:
        """Get artist info.

        NOTE: Artist.popularity was REMOVED in Feb 2026. We use followers
        count as the proxy signal for "size" of an artist.
        """
        return self._sp._get(f"artists/{artist_id}")

    # ───────────────────────────────────────────────────────────
    # SEARCH (with reduced limit: 10 max, 5 default)
    # ───────────────────────────────────────────────────────────

    def search_artists(
        self,
        query: str,
        limit: int = 10,  # max 10 in Feb 2026
        offset: int = 0,
    ) -> dict:
        """Search for artists.

        NOTE: limit reduced from 50 → 10 in Feb 2026.

        Args:
            query: search query (e.g. 'genre:"indie rock" year:2020-2024')
            limit: max 10

        Returns:
            dict with 'artists' key containing 'items' list
        """
        if limit > 10:
            limit = 10
            print(f"⚠️  Spotify reduced search limit to 10 in Feb 2026, capped to {limit}")
        return self._sp._get(
            "search",
            q=query,
            type="artist",
            limit=limit,
            offset=offset,
        )
