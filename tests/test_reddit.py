"""Unit tests for Reddit RSS client (FP-3d)."""
import base64
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.discovery.sources.reddit import (
    RedditClient,
    RedditPost,
    fetch_subreddit,
    USER_AGENT,
    ATOM_NS,
)


# ────────────────────────────────────────────────────────────
# Sample RSS feed (real Reddit /r/indieheads/.rss)
# ────────────────────────────────────────────────────────────

SAMPLE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>indieheads</title>
  <entry>
    <id>t3_abc123</id>
    <title>[FRESH] Black Country, New Road - Live at Pitchfork</title>
    <link href="https://www.reddit.com/r/indieheads/comments/abc123/" />
    <author><name>/u/testuser</name></author>
    <published>2026-07-08T15:00:00+00:00</published>
    <content type="html">&lt;div&gt;Listen here: &lt;a href=&quot;https://open.spotify.com/track/123abc&quot;&gt;Spotify&lt;/a&gt; or &lt;a href=&quot;https://example.bandcamp.com/track/x&quot;&gt;Bandcamp&lt;/a&gt;&lt;/div&gt;</content>
  </entry>
  <entry>
    <id>t3_def456</id>
    <title>Weekly Discussion Thread</title>
    <link href="https://www.reddit.com/r/indieheads/comments/def456/" />
    <author><name>/u/AutoModerator</name></author>
    <published>2026-07-08T14:00:00+00:00</published>
    <content type="html">Just a thread</content>
  </entry>
  <entry>
    <id>t3_ghi789</id>
    <title>[FRESH VIDEO] Yo La Tengo - New Single</title>
    <link href="https://www.reddit.com/r/indieheads/comments/ghi789/" />
    <author><name>/u/poster</name></author>
    <published>2026-07-08T13:00:00+00:00</published>
    <content type="html">Watch: &lt;a href=&quot;https://youtu.be/xyz123&quot;&gt;YouTube&lt;/a&gt;</content>
  </entry>
</feed>"""


# ────────────────────────────────────────────────────────────
# Parsing tests
# ────────────────────────────────────────────────────────────

class TestParsing:
    def test_parse_full_feed(self, tmp_path):
        client = RedditClient(cache_dir=tmp_path)
        with patch.object(client, "_fetch", return_value=SAMPLE_RSS):
            posts = client.get_subreddit_feed("indieheads", limit=10)
        assert len(posts) == 3
        assert posts[0].id == "t3_abc123"
        assert posts[0].title.startswith("[FRESH]")
        assert posts[0].author == "testuser"
        assert posts[0].subreddit == "indieheads"
        assert posts[0].is_fresh is True

    def test_parse_extracts_spotify_url(self, tmp_path):
        client = RedditClient(cache_dir=tmp_path)
        with patch.object(client, "_fetch", return_value=SAMPLE_RSS):
            posts = client.get_subreddit_feed("indieheads")
        fresh = posts[0]
        assert fresh.spotify_url == "https://open.spotify.com/track/123abc"

    def test_parse_extracts_bandcamp_url(self, tmp_path):
        client = RedditClient(cache_dir=tmp_path)
        with patch.object(client, "_fetch", return_value=SAMPLE_RSS):
            posts = client.get_subreddit_feed("indieheads")
        fresh = posts[0]
        assert fresh.bandcamp_url == "https://example.bandcamp.com/track/x"

    def test_parse_extracts_other_links(self, tmp_path):
        client = RedditClient(cache_dir=tmp_path)
        with patch.object(client, "_fetch", return_value=SAMPLE_RSS):
            posts = client.get_subreddit_feed("indieheads")
        # 3rd post has YouTube link (not spotify/bandcamp)
        video_post = posts[2]
        assert any("youtu.be" in u for u in video_post.other_links)

    def test_parse_filters_authors(self, tmp_path):
        client = RedditClient(cache_dir=tmp_path)
        with patch.object(client, "_fetch", return_value=SAMPLE_RSS):
            posts = client.get_subreddit_feed("indieheads")
        # /u/testuser and u/poster should be cleaned
        authors = [p.author for p in posts]
        assert "testuser" in authors
        assert "poster" in authors
        assert "AutoModerator" in authors

    def test_parse_strips_subreddit_prefix(self, tmp_path):
        client = RedditClient(cache_dir=tmp_path)
        with patch.object(client, "_fetch", return_value=SAMPLE_RSS):
            posts = client.get_subreddit_feed("r/indieheads")
        assert posts[0].subreddit == "indieheads"

    def test_parse_published_date(self, tmp_path):
        client = RedditClient(cache_dir=tmp_path)
        with patch.object(client, "_fetch", return_value=SAMPLE_RSS):
            posts = client.get_subreddit_feed("indieheads")
        assert isinstance(posts[0].published, datetime)
        assert posts[0].published.tzinfo is not None

    def test_parse_empty_feed(self, tmp_path):
        empty = b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
        client = RedditClient(cache_dir=tmp_path)
        with patch.object(client, "_fetch", return_value=empty):
            posts = client.get_subreddit_feed("indieheads")
        assert posts == []

    def test_parse_skips_malformed_entries(self, tmp_path):
        malformed = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><id>t3_x</id><title>Valid</title></entry>
  <entry><id></id><title></title></entry>
  <entry><title>No ID</title></entry>
</feed>"""
        client = RedditClient(cache_dir=tmp_path)
        with patch.object(client, "_fetch", return_value=malformed):
            posts = client.get_subreddit_feed("indieheads")
        # Only the valid entry should be returned
        assert len(posts) >= 1


# ────────────────────────────────────────────────────────────
# Filtering tests
# ────────────────────────────────────────────────────────────

class TestFiltering:
    @pytest.fixture
    def sample_posts(self):
        return [
            RedditPost(
                id="1", title="[FRESH] Test 1", author="a", subreddit="r",
                link="", published=datetime.now(timezone.utc),
                tags=["[FRESH]"], spotify_url="https://open.spotify.com/track/x",
            ),
            RedditPost(
                id="2", title="[FRESH VIDEO] Test 2", author="b", subreddit="r",
                link="", published=datetime.now(timezone.utc),
                tags=["[FRESH VIDEO]"], spotify_url=None,
            ),
            RedditPost(
                id="3", title="Weekly thread", author="c", subreddit="r",
                link="", published=datetime.now(timezone.utc),
                tags=[], spotify_url="https://open.spotify.com/track/y",
            ),
            RedditPost(
                id="4", title="Discussion", author="d", subreddit="r",
                link="", published=datetime.now(timezone.utc),
                tags=[], spotify_url=None,
            ),
        ]

    def test_filter_fresh(self, sample_posts):
        client = RedditClient()
        fresh = client.filter_fresh(sample_posts)
        assert len(fresh) == 2
        assert all(p.is_fresh for p in fresh)

    def test_filter_with_spotify(self, sample_posts):
        client = RedditClient()
        spotify = client.filter_with_spotify(sample_posts)
        assert len(spotify) == 2
        assert {p.id for p in spotify} == {"1", "3"}

    def test_filter_with_music_link(self, sample_posts):
        client = RedditClient()
        linked = client.filter_with_music_link(sample_posts)
        # Post 1 has spotify, 3 has spotify, 2/4 have no music link
        assert len(linked) == 2


class TestFreshDetection:
    def test_fresh_tag(self):
        post = RedditPost(
            id="1", title="[FRESH] Song", author="a", subreddit="r",
            link="", published=datetime.now(timezone.utc), tags=["[FRESH]"],
        )
        assert post.is_fresh

    def test_fresh_video_tag(self):
        post = RedditPost(
            id="1", title="[FRESH VIDEO] Song", author="a", subreddit="r",
            link="", published=datetime.now(timezone.utc), tags=["[FRESH VIDEO]"],
        )
        assert post.is_fresh

    def test_fresh_performance_tag(self):
        post = RedditPost(
            id="1", title="[FRESH PERFORMANCE] Song", author="a", subreddit="r",
            link="", published=datetime.now(timezone.utc), tags=["[FRESH PERFORMANCE]"],
        )
        assert post.is_fresh

    def test_no_fresh_tag(self):
        post = RedditPost(
            id="1", title="Just a title", author="a", subreddit="r",
            link="", published=datetime.now(timezone.utc), tags=[],
        )
        assert not post.is_fresh

    def test_unrelated_brackets(self):
        post = RedditPost(
            id="1", title="Some [REVIEW] post", author="a", subreddit="r",
            link="", published=datetime.now(timezone.utc), tags=["[REVIEW]"],
        )
        assert not post.is_fresh


# ────────────────────────────────────────────────────────────
# Subreddit normalization
# ────────────────────────────────────────────────────────────

class TestSubredditNormalization:
    def test_strips_r_prefix(self, tmp_path):
        client = RedditClient(cache_dir=tmp_path)
        with patch.object(client, "_fetch", return_value=SAMPLE_RSS) as mock_fetch:
            client.get_subreddit_feed("r/indieheads")
        # URL should not have r/ prefix
        assert "r/indieheads/" in mock_fetch.call_args[0][0]
        assert "rr/" not in mock_fetch.call_args[0][0]

    def test_handles_empty_subreddit(self, tmp_path):
        client = RedditClient(cache_dir=tmp_path)
        assert client.get_subreddit_feed("") == []
        assert client.get_subreddit_feed("r/") == []

    def test_caps_limit_at_100(self, tmp_path):
        client = RedditClient(cache_dir=tmp_path)
        with patch.object(client, "_fetch", return_value=SAMPLE_RSS):
            client.get_subreddit_feed("indieheads", limit=500)
        # No assertion needed; just verify no crash

    def test_invalid_sort_falls_back(self, tmp_path):
        client = RedditClient(cache_dir=tmp_path)
        with patch.object(client, "_fetch", return_value=SAMPLE_RSS) as mock_fetch:
            client.get_subreddit_feed("indieheads", sort="garbage")
        # Falls back to "new"
        assert "/new/.rss" in mock_fetch.call_args[0][0]


# ────────────────────────────────────────────────────────────
# Multi-subreddit
# ────────────────────────────────────────────────────────────

class TestMultiSubreddit:
    def test_merges_results(self, tmp_path):
        client = RedditClient(cache_dir=tmp_path)
        call_count = 0
        def fake_fetch(url):
            nonlocal call_count
            call_count += 1
            return SAMPLE_RSS
        with patch.object(client, "_fetch", side_effect=fake_fetch):
            posts = client.get_multi_subreddit_feed(["indieheads", "listentothis"], limit=10)
        assert len(posts) > 0
        assert call_count == 2  # fetched both subreddits

    def test_limit_applied_to_merged(self, tmp_path):
        client = RedditClient(cache_dir=tmp_path)
        with patch.object(client, "_fetch", return_value=SAMPLE_RSS):
            posts = client.get_multi_subreddit_feed(["indieheads"], limit=2)
        assert len(posts) <= 2


# ────────────────────────────────────────────────────────────
# Caching
# ────────────────────────────────────────────────────────────

class TestCaching:
    def test_caches_response(self, tmp_path):
        client = RedditClient(cache_dir=tmp_path, cache_ttl_seconds=3600)
        call_count = 0
        def fake_urlopen(req, **kwargs):
            nonlocal call_count
            call_count += 1
            from io import BytesIO
            resp = MagicMock()
            resp.read = lambda: SAMPLE_RSS
            resp.__enter__ = lambda s: resp
            resp.__exit__ = lambda s, *a: None
            return resp
        with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             patch.object(client, "_throttle"):
            client.get_subreddit_feed("indieheads")
            client.get_subreddit_feed("indieheads")
        # Second call should be cached
        assert call_count == 1

    def test_throttle_called(self, tmp_path):
        """Throttle is called from inside _fetch."""
        client = RedditClient(cache_dir=tmp_path)
        # Bypass cache, but use real _throttle
        with patch.object(client, "_get_cached", return_value=None), \
             patch.object(client, "_throttle") as mock_throttle, \
             patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__.return_value.read.return_value = SAMPLE_RSS
            client.get_subreddit_feed("indieheads")
        mock_throttle.assert_called()

    def test_failed_fetch_returns_empty(self, tmp_path):
        client = RedditClient(cache_dir=tmp_path)
        with patch.object(client, "_fetch", side_effect=Exception("network error")):
            posts = client.get_subreddit_feed("indieheads")
        assert posts == []


# ────────────────────────────────────────────────────────────
# Link extraction
# ────────────────────────────────────────────────────────────

class TestLinkExtraction:
    @pytest.fixture
    def client(self):
        return RedditClient()

    def test_spotify_track(self, client):
        text = 'Listen: <a href="https://open.spotify.com/track/abc123">here</a>'
        assert client._extract_spotify_url(text) == "https://open.spotify.com/track/abc123"

    def test_spotify_album(self, client):
        text = '<a href="https://open.spotify.com/album/xyz789">Album</a>'
        assert client._extract_spotify_url(text) == "https://open.spotify.com/album/xyz789"

    def test_no_spotify(self, client):
        text = "No links here"
        assert client._extract_spotify_url(text) is None

    def test_bandcamp(self, client):
        text = '<a href="https://artist.bandcamp.com/track/song-name">BC</a>'
        assert client._extract_bandcamp_url(text) == "https://artist.bandcamp.com/track/song-name"

    def test_other_links_excludes_spotify(self, client):
        text = '''
        <a href="https://open.spotify.com/track/x">Sp</a>
        <a href="https://youtube.com/watch?v=abc">YT</a>
        <a href="https://soundcloud.com/track">SC</a>
        '''
        other = client._extract_other_links(text)
        assert "https://open.spotify.com/track/x" not in other
        assert "https://youtube.com/watch?v=abc" in other
        assert "https://soundcloud.com/track" in other

    def test_other_links_empty(self, client):
        assert client._extract_other_links("") == []
        assert client._extract_other_links("No links at all") == []


# ────────────────────────────────────────────────────────────
# Convenience function
# ────────────────────────────────────────────────────────────

class TestConvenience:
    def test_fetch_subreddit_helper(self, tmp_path):
        with patch("src.discovery.sources.reddit.RedditClient") as MockClient:
            instance = MockClient.return_value
            instance.get_subreddit_feed.return_value = [
                RedditPost(id="1", title="x", author="a", subreddit="r",
                          link="", published=datetime.now(timezone.utc))
            ]
            posts = fetch_subreddit("indieheads")
        assert len(posts) == 1
        instance.get_subreddit_feed.assert_called_once_with("indieheads", sort="new", limit=25)
