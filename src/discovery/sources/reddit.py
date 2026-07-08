"""Reddit RSS client for community-driven music discovery.

Reddit's .rss endpoints (https://www.reddit.com/r/<subreddit>/.rss) are
publicly available and require no authentication. The same endpoints are
used by RSS readers, so they're not rate-limited like the JSON API.

Used to surface:
- Trending/new music from niche communities
- [FRESH] tags (new releases)
- Community recommendations and weekly threads
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import feedparser  # type: ignore[import-untyped]

log = logging.getLogger(__name__)

USER_AGENT = "spotify-curator/0.2.0 (https://github.com/petekaik/spotify-curator)"
DEFAULT_CACHE_DIR = Path.home() / ".spotify-curator" / "reddit_cache"
RATE_LIMIT_SECONDS = 2.0  # 2 sec between requests to be polite

# Atom namespace used in Reddit's RSS
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


@dataclass
class RedditPost:
    """Single Reddit post from RSS feed."""
    id: str                          # Reddit post ID (e.g. "t3_1uqsk4s")
    title: str
    author: str                      # username (without /u/)
    subreddit: str                   # subreddit name (without r/)
    link: str                        # full URL to post
    published: datetime              # post timestamp (UTC)
    summary: str = ""                # text content
    score: int = 0                   # upvote count (if visible)
    comments: int = 0                # comment count (if visible)
    tags: list[str] = field(default_factory=list)  # [FRESH], [LIVE], etc.
    spotify_url: Optional[str] = None  # extracted Spotify link if any
    bandcamp_url: Optional[str] = None  # extracted Bandcamp link if any
    other_links: list[str] = field(default_factory=list)

    @property
    def is_fresh(self) -> bool:
        """New release post (industry shorthand)."""
        return any(t.lower() in ("[fresh]", "[fresh video]", "[fresh performance]")
                   for t in self.tags)


class RedditClient:
    """Thin Reddit RSS wrapper with on-disk caching.

    Usage:
        >>> client = RedditClient()
        >>> posts = client.get_subreddit_feed("indieheads", limit=25)
        >>> for post in posts:
        ...     if post.is_fresh:
        ...         print(post.title, post.spotify_url)
    """

    def __init__(
        self,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        cache_ttl_seconds: int = 6 * 3600,  # 6 hours
    ):
        self._cache_dir = cache_dir
        self._cache_ttl = cache_ttl_seconds
        self._last_request = 0.0
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _throttle(self) -> None:
        """Sleep if needed to respect rate limit."""
        now = time.time()
        elapsed = now - self._last_request
        if elapsed < RATE_LIMIT_SECONDS:
            time.sleep(RATE_LIMIT_SECONDS - elapsed)
        self._last_request = time.time()

    def _cache_path(self, key: str) -> Path:
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

    def _fetch(self, url: str) -> bytes:
        """Fetch URL with throttling and caching.

        Returns raw bytes (RSS XML).
        """
        cached = self._get_cached(url)
        if cached is not None:
            log.debug(f"Reddit cache hit: {url}")
            # Cached data is the raw bytes as base64-encoded string
            import base64
            return base64.b64decode(cached["b64"])

        self._throttle()
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        log.debug(f"Reddit fetch: {url}")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
        # Cache as base64
        import base64
        self._save_cache(url, {"b64": base64.b64encode(data).decode()})
        return data

    def get_subreddit_feed(
        self,
        subreddit: str,
        sort: str = "new",   # new, hot, top, rising
        limit: int = 25,
    ) -> list[RedditPost]:
        """Fetch posts from a subreddit.

        Args:
            subreddit: subreddit name (without r/)
            sort: "new" (default), "hot", "top", "rising"
            limit: max posts to return (max 100)

        Returns:
            list of RedditPost, most recent first
        """
        subreddit = subreddit.strip().lstrip("r/").lstrip("/")
        if not subreddit:
            return []
        if sort not in ("new", "hot", "top", "rising", "controversial"):
            sort = "new"
        if limit > 100:
            limit = 100
        url = f"https://www.reddit.com/r/{subreddit}/{sort}/.rss"
        try:
            data = self._fetch(url)
        except Exception as e:
            log.warning(f"Failed to fetch r/{subreddit}: {e}")
            return []
        return self._parse_feed(data, subreddit, limit)

    def get_multi_subreddit_feed(
        self,
        subreddits: list[str],
        sort: str = "new",
        limit: int = 50,
    ) -> list[RedditPost]:
        """Fetch posts from multiple subreddits, sorted by recency.

        Args:
            subreddits: list of subreddit names
            sort: sort type per subreddit
            limit: max total posts to return

        Returns:
            merged list of RedditPost, newest first
        """
        all_posts: list[RedditPost] = []
        per_sub = max(5, limit // max(1, len(subreddits)))
        for sub in subreddits:
            all_posts.extend(self.get_subreddit_feed(sub, sort=sort, limit=per_sub))
        # Sort by published date desc
        all_posts.sort(key=lambda p: p.published, reverse=True)
        return all_posts[:limit]

    # ────────────────────────────────────────────────────────────
    # Parsing
    # ────────────────────────────────────────────────────────────

    def _parse_feed(
        self,
        data: bytes,
        subreddit: str,
        limit: int,
    ) -> list[RedditPost]:
        """Parse RSS XML to list of RedditPost."""
        # Use feedparser for robust XML/Atom handling
        parsed = feedparser.parse(data)
        if not parsed.entries:
            # Fallback to stdlib ET if feedparser fails
            return self._parse_feed_et(data, subreddit, limit)

        posts: list[RedditPost] = []
        for entry in parsed.entries[:limit]:
            post = self._entry_to_post(entry, subreddit)
            if post:
                posts.append(post)
        return posts

    def _entry_to_post(self, entry: dict, subreddit: str) -> Optional[RedditPost]:
        """Convert a feedparser entry to RedditPost."""
        try:
            post_id = entry.get("id", "")
            title = entry.get("title", "")
            if not post_id or not title:
                return None

            # Author
            author = entry.get("author", "")
            if author.startswith("/u/"):
                author = author[3:]
            elif author.startswith("u/"):
                author = author[2:]

            # Link
            link = entry.get("link", "")

            # Published date
            published_str = entry.get("published", "") or entry.get("updated", "")
            try:
                # feedparser returns time.struct_time
                published_tuple = entry.get("published_parsed") or entry.get("updated_parsed")
                if published_tuple:
                    published = datetime(*published_tuple[:6], tzinfo=timezone.utc)
                else:
                    published = datetime.now(timezone.utc)
            except Exception:
                published = datetime.now(timezone.utc)

            # Summary / content
            summary = entry.get("summary", "") or entry.get("description", "")

            # Extract tags from title ([FRESH], [LIVE], [FRESH VIDEO], etc.)
            tags = re.findall(r"\[[^\]]+\]", title)

            # Extract music service links from summary
            spotify_url = self._extract_spotify_url(summary)
            bandcamp_url = self._extract_bandcamp_url(summary)
            other_links = self._extract_other_links(summary)

            return RedditPost(
                id=post_id,
                title=title,
                author=author,
                subreddit=subreddit,
                link=link,
                published=published,
                summary=summary,
                tags=tags,
                spotify_url=spotify_url,
                bandcamp_url=bandcamp_url,
                other_links=other_links,
            )
        except Exception as e:
            log.debug(f"Failed to parse entry: {e}")
            return None

    def _parse_feed_et(
        self,
        data: bytes,
        subreddit: str,
        limit: int,
    ) -> list[RedditPost]:
        """Fallback parser using stdlib ElementTree."""
        try:
            root = ET.fromstring(data)
        except ET.ParseError as e:
            log.warning(f"Failed to parse RSS XML: {e}")
            return []
        posts: list[RedditPost] = []
        for entry in root.findall("atom:entry", ATOM_NS)[:limit]:
            post_id = entry.findtext("atom:id", "", ATOM_NS)
            title = entry.findtext("atom:title", "", ATOM_NS)
            link_el = entry.find("atom:link", ATOM_NS)
            link = link_el.get("href", "") if link_el is not None else ""
            author_el = entry.find("atom:author/atom:name", ATOM_NS)
            author = (author_el.text or "").replace("/u/", "").replace("u/", "") if author_el is not None else ""
            published_str = entry.findtext("atom:published", "", ATOM_NS)
            try:
                published = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                published = datetime.now(timezone.utc)
            summary = entry.findtext("atom:content", "", ATOM_NS) or entry.findtext("atom:summary", "", ATOM_NS)
            tags = re.findall(r"\[[^\]]+\]", title)
            spotify_url = self._extract_spotify_url(summary)
            bandcamp_url = self._extract_bandcamp_url(summary)
            other_links = self._extract_other_links(summary)
            if post_id and title:
                posts.append(RedditPost(
                    id=post_id, title=title, author=author,
                    subreddit=subreddit, link=link, published=published,
                    summary=summary, tags=tags,
                    spotify_url=spotify_url, bandcamp_url=bandcamp_url,
                    other_links=other_links,
                ))
        return posts

    # ────────────────────────────────────────────────────────────
    # Link extraction helpers
    # ────────────────────────────────────────────────────────────

    _SPOTIFY_RE = re.compile(
        r"https?://open\.spotify\.com/(?:track|album|playlist|artist)/[A-Za-z0-9]+"
    )
    _BANDCAMP_RE = re.compile(r"https?://[a-zA-Z0-9_-]+\.bandcamp\.com/[^\s\"']*")

    def _extract_spotify_url(self, text: str) -> Optional[str]:
        if not text:
            return None
        m = self._SPOTIFY_RE.search(text)
        return m.group(0) if m else None

    def _extract_bandcamp_url(self, text: str) -> Optional[str]:
        if not text:
            return None
        m = self._BANDCAMP_RE.search(text)
        return m.group(0) if m else None

    def _extract_other_links(self, text: str) -> list[str]:
        """Extract non-Spotify/Bandcamp music links."""
        if not text:
            return []
        # Find all href URLs
        urls = re.findall(r'href="(https?://[^"]+)"', text)
        # Filter out music platforms we already track
        other = [
            u for u in urls
            if "open.spotify.com" not in u and "bandcamp.com" not in u
            and "reddit.com" not in u
        ]
        return other[:5]  # cap

    # ────────────────────────────────────────────────────────────
    # Filtering helpers (used by callers)
    # ────────────────────────────────────────────────────────────

    def filter_fresh(self, posts: list[RedditPost]) -> list[RedditPost]:
        """Return only [FRESH]-tagged posts."""
        return [p for p in posts if p.is_fresh]

    def filter_with_spotify(self, posts: list[RedditPost]) -> list[RedditPost]:
        """Return posts that contain a Spotify link (resolvable to tracks)."""
        return [p for p in posts if p.spotify_url]

    def filter_with_music_link(
        self, posts: list[RedditPost],
    ) -> list[RedditPost]:
        """Return posts with any music-platform link."""
        return [p for p in posts if p.spotify_url or p.bandcamp_url or p.other_links]


# Convenience function
def fetch_subreddit(
    subreddit: str,
    sort: str = "new",
    limit: int = 25,
) -> list[RedditPost]:
    """One-shot: fetch posts from a subreddit."""
    client = RedditClient()
    return client.get_subreddit_feed(subreddit, sort=sort, limit=limit)
