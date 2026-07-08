"""Last.fm API client for emerging artist discovery.

Last.fm is the primary source for "emerging" signals because:
- Real listening data via scrobbles (community-curated)
- tag.getTopArtists by period (1week, 1month, 3month, 6month, 12month, overall)
- artist.getSimilar for graph expansion
- artist.getInfo for scrobble counts and bio

No auth needed for read-only endpoints — just an API key.
Apply for one at https://www.last.fm/api/account/create

API docs: https://www.last.fm/api
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import requests

log = logging.getLogger(__name__)

API_BASE = "https://ws.audioscrobbler.com/2.0/"
DEFAULT_CACHE_DIR = Path.home() / ".spotify-curator" / "lastfm_cache"


@dataclass
class LastfmArtist:
    """Artist data from Last.fm."""
    name: str
    mbid: Optional[str] = None           # MusicBrainz ID
    url: str = ""
    playcount: int = 0                   # total scrobbles
    listeners: int = 0                   # distinct users
    tags: list[str] = field(default_factory=list)
    bio_summary: str = ""
    similar: list[str] = field(default_factory=list)  # similar artist names


def _load_api_key() -> str:
    """Load LASTFM_API_KEY from .env or environment."""
    try:
        from dotenv import load_dotenv
        env_path = Path(__file__).parent.parent.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)
    except ImportError:
        pass

    key = os.getenv("LASTFM_API_KEY")
    if not key:
        log.warning(
            "LASTFM_API_KEY not found in environment. "
            "Last.fm requests will fail. "
            "Get a key at https://www.last.fm/api/account/create"
        )
    return key or ""


def _cache_path(method: str, params: dict, cache_dir: Path) -> Path:
    """Build a cache file path from method+params."""
    key = f"{method}_{json.dumps(params, sort_keys=True)}"
    # Hash to safe filename
    import hashlib
    h = hashlib.sha256(key.encode()).hexdigest()[:16]
    return cache_dir / f"{method}_{h}.json"


class LastfmClient:
    """Thin Last.fm API wrapper with on-disk caching.

    Usage:
        >>> client = LastfmClient(api_key="...")
        >>> artists = client.tag_get_top_artists("indie rock", period="6month", limit=30)
        >>> for a in artists:
        ...     print(a.name, a.listeners)

    The client transparently caches responses to ~/.spotify-curator/lastfm_cache/
    so repeated runs are fast and don't hit the rate limit.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        cache_ttl_seconds: int = 7 * 24 * 3600,  # 1 week
        min_request_interval: float = 0.25,        # 4 req/s
    ):
        """Initialize client.

        Args:
            api_key: Last.fm API key (loads from env if None)
            cache_dir: where to cache responses
            cache_ttl_seconds: cache lifetime (default 1 week)
            min_request_interval: minimum seconds between API calls
        """
        self._api_key = api_key or _load_api_key()
        self._cache_dir = cache_dir
        self._cache_ttl = cache_ttl_seconds
        self._min_interval = min_request_interval
        self._last_request = 0.0
        self._session = requests.Session()
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _throttle(self) -> None:
        """Sleep if needed to respect rate limit."""
        now = time.time()
        elapsed = now - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request = time.time()

    def _get_cached(self, method: str, params: dict) -> Optional[dict]:
        """Read from cache if fresh."""
        path = _cache_path(method, params, self._cache_dir)
        if not path.exists():
            return None
        try:
            mtime = path.stat().st_mtime
            if (time.time() - mtime) > self._cache_ttl:
                return None
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def _save_cache(self, method: str, params: dict, response: dict) -> None:
        path = _cache_path(method, params, self._cache_dir)
        try:
            path.write_text(json.dumps(response))
        except OSError as e:
            log.debug(f"Cache write failed: {e}")

    def _call(self, method: str, **params) -> dict:
        """Make an API call with throttling and caching.

        Args:
            method: Last.fm method name (e.g. "tag.getTopArtists")
            **params: method-specific parameters

        Returns:
            response JSON (the part inside the JSON root)

        Raises:
            RuntimeError: on API errors
        """
        if not self._api_key:
            raise RuntimeError("LASTFM_API_KEY not set")

        cached = self._get_cached(method, params)
        if cached is not None:
            log.debug(f"Last.fm cache hit: {method} {params}")
            return cached

        self._throttle()
        query = {
            "method": method,
            "api_key": self._api_key,
            "format": "json",
            **params,
        }
        url = f"{API_BASE}?{urlencode(query)}"
        log.debug(f"Last.fm GET {method} {params}")

        resp = self._session.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if "error" in data:
            raise RuntimeError(
                f"Last.fm error {data['error']}: {data.get('message')}"
            )

        self._save_cache(method, params, data)
        return data

    # ────────────────────────────────────────────────────────────
    # Public API methods
    # ────────────────────────────────────────────────────────────

    def tag_get_top_artists(
        self,
        tag: str,
        period: str = "6month",
        limit: int = 50,
        page: int = 1,
    ) -> list[LastfmArtist]:
        """Get top artists for a tag in a time period.

        Args:
            tag: tag name (e.g. "indie rock", "shoegaze", "post-rock")
            period: "overall" | "7day" | "1month" | "3month" | "6month" | "12month"
            limit: max 1000 (but we cap at 50 for sanity)
            page: pagination

        Returns:
            list of LastfmArtist, ordered by listener count
        """
        data = self._call(
            "tag.getTopArtists",
            tag=tag,
            period=period,
            limit=min(limit, 1000),
            page=page,
        )
        artists_raw = data.get("topartists", {}).get("artist", [])
        return [self._parse_artist(a) for a in artists_raw]

    def artist_get_info(
        self,
        artist: str,
        mbid: Optional[str] = None,
    ) -> LastfmArtist:
        """Get detailed info about an artist.

        Args:
            artist: artist name (or pass mbid= for canonical lookup)
            mbid: MusicBrainz ID (preferred for accuracy)

        Returns:
            LastfmArtist with full bio, stats, tags
        """
        params = {"artist": artist} if not mbid else {"mbid": mbid}
        data = self._call("artist.getInfo", **params)
        a = data.get("artist", {})
        return self._parse_artist(a, include_similar=True)

    def artist_get_similar(
        self,
        artist: str,
        mbid: Optional[str] = None,
        limit: int = 30,
    ) -> list[str]:
        """Get similar artists by name.

        Args:
            artist: artist name (or mbid)
            limit: max 100

        Returns:
            list of similar artist names
        """
        params = {"artist": artist, "limit": min(limit, 100)} if not mbid else {"mbid": mbid, "limit": min(limit, 100)}
        data = self._call("artist.getSimilar", **params)
        similar = data.get("similarartists", {}).get("artist", [])
        return [a.get("name", "") for a in similar if a.get("name")]

    def _parse_artist(self, raw: dict, include_similar: bool = False) -> LastfmArtist:
        """Parse a Last.fm artist dict into LastfmArtist."""
        tags_raw = raw.get("tags", {}).get("tag", [])
        tags = [t.get("name", "") for t in tags_raw if t.get("name")]

        bio = raw.get("bio", {})
        bio_summary = bio.get("summary", "") if isinstance(bio, dict) else ""

        similar = []
        if include_similar:
            similar_raw = raw.get("similar", {}).get("artist", [])
            similar = [a.get("name", "") for a in similar_raw if a.get("name")]

        stats = raw.get("stats", {})
        listeners = 0
        playcount = 0
        if isinstance(stats, dict):
            try:
                listeners = int(stats.get("listeners", "0"))
            except (ValueError, TypeError):
                pass
            try:
                playcount = int(stats.get("playcount", "0"))
            except (ValueError, TypeError):
                pass

        return LastfmArtist(
            name=raw.get("name", ""),
            mbid=raw.get("mbid") or None,
            url=raw.get("url", ""),
            playcount=playcount,
            listeners=listeners,
            tags=tags,
            bio_summary=bio_summary,
            similar=similar,
        )


# Convenience function for one-off calls
def get_top_artists_for_tag(
    tag: str,
    period: str = "6month",
    limit: int = 30,
    api_key: Optional[str] = None,
) -> list[LastfmArtist]:
    """One-shot helper: top artists for a tag.

    Args:
        tag: e.g. "indie rock"
        period: see LastfmClient.tag_get_top_artists
        limit: max artists to return
        api_key: Last.fm API key (loads from env if None)
    """
    client = LastfmClient(api_key=api_key)
    return client.tag_get_top_artists(tag, period=period, limit=limit)
