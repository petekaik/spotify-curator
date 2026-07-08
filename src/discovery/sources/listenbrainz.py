"""ListenBrainz Labs API client for similarity-based discovery.

ListenBrainz is a project of the MetaBrainz foundation (same people as
MusicBrainz). Their "Labs" API provides ML-derived similar artists based
on actual listening data submitted by users — no Spotify popularity
bias.

The key endpoint: `similar-artists` takes a list of MusicBrainz IDs
(MBIDs) and returns similar artists with similarity scores.

API docs: https://listenbrainz.readthedocs.io/en/latest/users/api/index.html
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import requests

log = logging.getLogger(__name__)

LABS_BASE = "https://labs.api.listenbrainz.org"
DEFAULT_CACHE_DIR = Path.home() / ".spotify-curator" / "listenbrainz_cache"


@dataclass
class SimilarArtist:
    """An artist similar to one of the seed artists."""
    name: str
    mbid: str                           # MusicBrainz ID
    similarity_score: float             # 0.0-1.0, higher = more similar


class ListenBrainzClient:
    """ListenBrainz Labs API wrapper with on-disk caching.

    Usage:
        >>> client = ListenBrainzClient()
        >>> similar = client.get_similar_artists(mbids=["mbid1", "mbid2"])
        >>> for s in similar:
        ...     print(s.name, s.similarity_score)

    No auth required. Be polite: 1 req/sec rate limit.
    """

    def __init__(
        self,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        cache_ttl_seconds: int = 7 * 24 * 3600,
        min_request_interval: float = 1.0,    # 1 req/s (be polite)
    ):
        self._cache_dir = cache_dir
        self._cache_ttl = cache_ttl_seconds
        self._min_interval = min_request_interval
        self._last_request = 0.0
        self._session = requests.Session()
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _throttle(self) -> None:
        now = time.time()
        elapsed = now - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request = time.time()

    def _cache_path(self, key_data: str) -> Path:
        import hashlib
        h = hashlib.sha256(key_data.encode()).hexdigest()[:16]
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

    def _save_cache(self, key: str, response: dict) -> None:
        path = self._cache_path(key)
        try:
            path.write_text(json.dumps(response))
        except OSError as e:
            log.debug(f"Cache write failed: {e}")

    def _get(self, endpoint: str, params: Optional[dict] = None) -> dict:
        """GET request with caching and throttling."""
        params = params or {}
        cache_key = f"GET {endpoint} {json.dumps(params, sort_keys=True)}"

        cached = self._get_cached(cache_key)
        if cached is not None:
            log.debug(f"ListenBrainz cache hit: {endpoint}")
            return cached

        self._throttle()
        url = f"{LABS_BASE}{endpoint}"
        if params:
            url += f"?{urlencode(params)}"

        log.debug(f"ListenBrainz GET {url}")
        resp = self._session.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        self._save_cache(cache_key, data)
        return data

    def _post(self, endpoint: str, body: dict) -> dict:
        """POST request with caching and throttling."""
        cache_key = f"POST {endpoint} {json.dumps(body, sort_keys=True)}"

        cached = self._get_cached(cache_key)
        if cached is not None:
            log.debug(f"ListenBrainz cache hit: {endpoint}")
            return cached

        self._throttle()
        url = f"{LABS_BASE}{endpoint}"
        log.debug(f"ListenBrainz POST {url} {body}")
        resp = self._session.post(url, json=body, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        self._save_cache(cache_key, data)
        return data

    # ────────────────────────────────────────────────────────────
    # Public API methods
    # ────────────────────────────────────────────────────────────

    def get_similar_artists(
        self,
        mbids: list[str],
        algorithm: str = "session_based_days_7500_session_300m",
    ) -> list[SimilarArtist]:
        """Get similar artists from a list of seed artist MBIDs.

        Args:
            mbids: list of MusicBrainz IDs (max ~25)
            algorithm: similarity algorithm. Options:
                - "session_based_days_7500_session_300m" (default, recent sessions)
                - "session_based_days_9000_session_300m" (older)
                - "recording_ref_link" (recording-level)
            Max seeds: 25

        Returns:
            list of SimilarArtist, ordered by similarity (highest first)
        """
        # Filter out empty MBIDs (None, "", whitespace-only)
        mbids = [m for m in mbids if m and m.strip()]
        if not mbids:
            return []
        if len(mbids) > 25:
            mbids = mbids[:25]

        # The API expects the algorithm name as a query param,
        # the mbids in the body as a list of strings
        try:
            data = self._post(
                f"/similar-artists/{algorithm}",
                {"mbids": mbids},
            )
        except requests.HTTPError as e:
            # Some algorithm names return 400 — fall back to default
            log.warning(f"ListenBrainz algorithm {algorithm} failed: {e}, using default")
            if algorithm != "session_based_days_7500_session_300m":
                data = self._post(
                    "/similar-artists/session_based_days_7500_session_300m",
                    {"mbids": mbids},
                )
            else:
                raise

        # Response shape (per docs):
        # [
        #   {"name": "Radiohead", "mbid": "...", "score": 0.87},
        #   ...
        # ]
        # The API may wrap it in a dict with a key like 'similar_artists' or
        # return a list directly. Handle both.
        if isinstance(data, dict):
            items = data.get("similar_artists", data.get("payload", []))
        else:
            items = data

        similar: list[SimilarArtist] = []
        for item in items:
            try:
                mbid = item.get("mbid") or ""
                name = item.get("name") or item.get("artist_name") or ""
                score = float(item.get("score", 0.0))
                if not mbid or not name:
                    continue
                similar.append(SimilarArtist(
                    name=name,
                    mbid=mbid,
                    similarity_score=score,
                ))
            except (ValueError, TypeError, AttributeError):
                continue

        # Sort by score desc (defensive)
        similar.sort(key=lambda s: s.similarity_score, reverse=True)
        return similar

    def get_fresh_releases(
        self,
        days: int = 30,
        limit: int = 100,
    ) -> list[dict]:
        """Get fresh releases (not artist-similarity related, but useful for FP-3+).

        Args:
            days: how many days back to look (max 90)
            limit: max releases to return

        Returns:
            list of release dicts with name, artist_mbid, release_date
        """
        if days > 90:
            days = 90
        data = self._get("/fresh-releases", {"days": days, "limit": min(limit, 100)})
        # Response: dict with 'payload' key containing 'releases' list
        releases = []
        payload = data.get("payload", {})
        for r in payload.get("releases", []):
            releases.append({
                "release_name": r.get("release_name", ""),
                "artist_name": r.get("artist_name", ""),
                "artist_mbid": r.get("artist_mbid", ""),
                "release_date": r.get("release_date", ""),
                "release_mbid": r.get("release_mbid", ""),
            })
        return releases


# Convenience function
def find_similar_to_artists(
    mbids: list[str],
    api_key_unused: Optional[str] = None,  # no key needed
) -> list[SimilarArtist]:
    """One-shot helper: find artists similar to given MBIDs.

    Note: api_key_unused is for API uniformity with LastfmClient.
    """
    client = ListenBrainzClient()
    return client.get_similar_artists(mbids)
