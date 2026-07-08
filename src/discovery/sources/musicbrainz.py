"""MusicBrainz API client for genre-rich artist discovery.

MusicBrainz is the most comprehensive open music database. It has:
- Rich genre taxonomy (community-curated tags + structured genres)
- Artist relations (member of band, collaborations)
- Free, no auth required (just User-Agent)

Used as the **secondary** discovery source because:
- Better genre data than Spotify (Spotify is deprecating genre data)
- No rate limits to speak of (1 req/sec is the polite recommendation)
- Works for "obscure but established" artists that Last.fm might miss
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import musicbrainzngs

log = logging.getLogger(__name__)

USER_AGENT = ("spotify-curator", "0.2.0",
              "https://github.com/petekaik/spotify-curator")
DEFAULT_CACHE_DIR = Path.home() / ".spotify-curator" / "musicbrainz_cache"
RATE_LIMIT_SECONDS = 1.0  # MusicBrainz recommends 1 req/sec


@dataclass
class MusicBrainzArtist:
    """Artist data from MusicBrainz."""
    name: str
    mbid: str                          # MusicBrainz ID
    sort_name: str = ""
    artist_type: str = ""              # "Person", "Group", "Orchestra", etc.
    country: str = ""
    begin_year: Optional[int] = None   # formed year
    end_year: Optional[int] = None     # disbanded year (None if active)
    tags: list[tuple[str, int]] = field(default_factory=list)  # (tag, count)
    genres: list[str] = field(default_factory=list)            # structured genres


class MusicBrainzClient:
    """Thin MusicBrainz API wrapper with on-disk caching.

    Usage:
        >>> client = MusicBrainzClient()
        >>> artist = client.get_artist_by_mbid("a74b1b7f-71a5-4011-9441-d0b5e4122711")
        >>> print(artist.genres)
        >>> results = client.search_artists_by_tag("post-rock", limit=30)
    """

    def __init__(
        self,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        cache_ttl_seconds: int = 7 * 24 * 3600,  # 1 week
    ):
        """Initialize client.

        Args:
            cache_dir: where to cache responses
            cache_ttl_seconds: cache lifetime (default 1 week)
        """
        # Set User-Agent (MusicBrainz requires this)
        musicbrainzngs.set_useragent(*USER_AGENT)

        self._cache_dir = cache_dir
        self._cache_ttl = cache_ttl_seconds
        self._last_request = 0.0
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _throttle(self) -> None:
        """Sleep if needed to respect 1 req/sec rate limit."""
        now = time.time()
        elapsed = now - self._last_request
        if elapsed < RATE_LIMIT_SECONDS:
            time.sleep(RATE_LIMIT_SECONDS - elapsed)
        self._last_request = time.time()

    def _cache_path(self, key: str) -> Path:
        import hashlib
        h = hashlib.sha256(key.encode()).hexdigest()[:16]
        return self._cache_dir / f"{h}.json"

    def _get_cached(self, key: str) -> Optional[dict]:
        path = self._cache_path(key)
        if not path.exists():
            return None
        try:
            mtime = path.stat().st_mtime
            if (time.time() - mtime) > self._cache_ttl:
                return None
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def _save_cache(self, key: str, data: dict) -> None:
        path = self._cache_path(key)
        try:
            path.write_text(json.dumps(data, default=str))
        except OSError as e:
            log.debug(f"Cache write failed: {e}")

    def _call(self, method_name: str, *args, **kwargs) -> dict:
        """Make a MusicBrainz API call with throttling and caching.

        Args:
            method_name: name of musicbrainzngs function (e.g. "search_artists")
            *args: positional arguments (e.g. mbid for get_artist_by_id)
            **kwargs: keyword arguments to that function

        Returns:
            raw API response dict
        """
        cache_key = f"{method_name}:{json.dumps({'args': args, 'kwargs': kwargs}, sort_keys=True, default=str)}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            log.debug(f"MusicBrainz cache hit: {method_name}")
            return cached

        self._throttle()
        method = getattr(musicbrainzngs, method_name)
        log.debug(f"MusicBrainz call: {method_name}(args={args}, kwargs={kwargs})")
        result = method(*args, **kwargs)
        # musicbrainzngs returns mbxml.Response or similar — convert to dict
        if hasattr(result, "to_dict"):
            result = result.to_dict()
        elif not isinstance(result, dict):
            result = {"raw": str(result)}
        self._save_cache(cache_key, result)
        return result

    # ────────────────────────────────────────────────────────────
    # Public API methods
    # ────────────────────────────────────────────────────────────

    def get_artist_by_mbid(self, mbid: str) -> Optional[MusicBrainzArtist]:
        """Get full artist data by MusicBrainz ID.

        Args:
            mbid: MusicBrainz UUID

        Returns:
            MusicBrainzArtist or None if not found / API error
        """
        if not mbid or not mbid.strip():
            return None
        try:
            # mbid is positional, includes is keyword in musicbrainzngs API
            result = self._call("get_artist_by_id", mbid, includes=["tags", "genres"])
        except musicbrainzngs.ResponseError as e:
            log.warning(f"MusicBrainz get_artist_by_id({mbid}) failed: {e}")
            return None
        except Exception as e:
            log.warning(f"MusicBrainz unexpected error: {e}")
            return None
        artist = self._parse_artist(result.get("artist", {}))
        # An empty artist dict means "not found"
        if not artist.mbid and not artist.name:
            return None
        return artist

    def search_artists(
        self,
        query: str,
        limit: int = 25,
        offset: int = 0,
    ) -> list[MusicBrainzArtist]:
        """Search artists by name (Lucene-like query syntax).

        Args:
            query: search string (e.g. "radiohead", "artist:radiohead")
            limit: max results (default 25, max 100)
            offset: pagination

        Returns:
            list of MusicBrainzArtist (name + mbid only, no full data)
            Use get_artist_by_mbid() for full details.
        """
        if limit > 100:
            limit = 100
        result = self._call("search_artists", query=query, limit=limit, offset=offset)
        artists_raw = result.get("artist-list", [])
        return [self._parse_artist(a, minimal=True) for a in artists_raw]

    def search_artists_by_tag(
        self,
        tag: str,
        limit: int = 25,
    ) -> list[MusicBrainzArtist]:
        """Search artists associated with a specific tag.

        Args:
            tag: tag name (e.g. "post-rock", "shoegaze", "ambient")
            limit: max results

        Returns:
            list of MusicBrainzArtist (name + mbid only)
        """
        return self.search_artists(f'tag:"{tag}"', limit=limit)

    def search_artists_by_genre(
        self,
        genre: str,
        limit: int = 25,
    ) -> list[MusicBrainzArtist]:
        """Search artists by structured genre.

        Args:
            genre: genre name (e.g. "indie rock", "dream pop")
            limit: max results

        Returns:
            list of MusicBrainzArtist (name + mbid only)
        """
        return self.search_artists(f'genre:"{genre}"', limit=limit)

    def search_artists_by_country(
        self,
        country: str,
        limit: int = 25,
    ) -> list[MusicBrainzArtist]:
        """Search artists by country (useful for geo_bonus).

        Args:
            country: ISO 3166-1 alpha-2 code (e.g. "FI", "SE") or full name
            limit: max results

        Returns:
            list of MusicBrainzArtist (name + mbid only)
        """
        return self.search_artists(f'area:"{country}"', limit=limit)

    def get_artist_relations(self, mbid: str) -> dict:
        """Get artist relations (member of band, collaborators, etc.).

        Returns raw relation dict. Useful for building a related-artist graph.
        """
        try:
            return self._call(
                "get_artist_by_id", mbid,
                includes=["artist-rels", "release-rels"],
            )
        except Exception as e:
            log.warning(f"get_artist_relations({mbid}) failed: {e}")
            return {}

    # ────────────────────────────────────────────────────────────
    # Parsing
    # ────────────────────────────────────────────────────────────

    def _parse_artist(self, raw: dict, minimal: bool = False) -> MusicBrainzArtist:
        """Parse a MusicBrainz artist dict into MusicBrainzArtist.

        Args:
            raw: artist dict from API
            minimal: if True, only parse name + mbid (for search results)
        """
        name = raw.get("name", "")
        mbid = raw.get("id", "")

        if minimal:
            return MusicBrainzArtist(name=name, mbid=mbid)

        # Sort name
        sort_name = raw.get("sort-name", "")

        # Type
        artist_type = raw.get("type", "")

        # Country / area
        area = raw.get("area") or {}
        country = area.get("name", "")

        # Lifespan
        lifespan = raw.get("life-span") or {}
        begin = lifespan.get("begin")
        end = lifespan.get("end")
        begin_year = int(begin[:4]) if begin and len(begin) >= 4 else None
        end_year = int(end[:4]) if end and len(end) >= 4 else None

        # Tags (user-submitted, with vote counts)
        tags_raw = raw.get("tag-list", []) or []
        tags: list[tuple[str, int]] = []
        if isinstance(tags_raw, list):
            for t in tags_raw:
                if isinstance(t, dict):
                    tag_name = t.get("name", "")
                    tag_count = int(t.get("count", 0))
                    if tag_name:
                        tags.append((tag_name, tag_count))
        # Sort by count desc
        tags.sort(key=lambda x: x[1], reverse=True)

        # Genres (structured, curated)
        genres_raw = raw.get("genre-list", []) or []
        genres: list[str] = []
        if isinstance(genres_raw, list):
            for g in genres_raw:
                if isinstance(g, dict):
                    gname = g.get("name", "")
                    if gname:
                        genres.append(gname)

        return MusicBrainzArtist(
            name=name,
            mbid=mbid,
            sort_name=sort_name,
            artist_type=artist_type,
            country=country,
            begin_year=begin_year,
            end_year=end_year,
            tags=tags,
            genres=genres,
        )

    def get_top_tags(self, artist: MusicBrainzArtist, n: int = 10) -> list[str]:
        """Get top N tag names (most-voted first) from a MusicBrainzArtist.

        Useful for feeding the ranking algorithm's tag-match logic.
        """
        return [t[0] for t in artist.tags[:n]]


# Convenience function
def search_artist_by_name(
    name: str,
    limit: int = 5,
) -> list[MusicBrainzArtist]:
    """One-shot: search artists by name, return up to 5 matches."""
    client = MusicBrainzClient()
    return client.search_artists(name, limit=limit)
