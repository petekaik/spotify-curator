"""Spotify API authentication wrapper.

Handles OAuth Authorization Code Flow with PKCE for Spotify Web API.
Uses spotipy under the hood but provides a clean interface and proper
token caching that survives restarts.

Usage:
    >>> from src.spotify.auth import get_spotify_client
    >>> sp = get_spotify_client()
    >>> user = sp.current_user()
    >>> print(user['display_name'])
"""
import os
import sys
from pathlib import Path
from typing import Optional

import spotipy
from spotipy.oauth2 import SpotifyOAuth

# Cache directory for OAuth tokens
CACHE_DIR = Path.home() / ".spotify-curator"
CACHE_FILE = CACHE_DIR / ".cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Scopes we need:
# - user-top-read: read user's top artists and tracks
# - user-library-read: read saved albums/tracks
# - user-library-modify: add/remove from library (NEW endpoint)
# - playlist-modify-public: create public playlists
# - playlist-modify-private: create private playlists
# - user-follow-read: read followed artists
SCOPES = (
    "user-top-read "
    "user-library-read "
    "user-library-modify "
    "playlist-modify-public "
    "playlist-modify-private "
    "user-follow-read"
)


def _load_credentials() -> tuple[str, str, str]:
    """Load Spotify API credentials from .env or environment.

    Returns:
        (client_id, client_secret, redirect_uri)
    """
    # Try to load .env
    try:
        from dotenv import load_dotenv
        env_path = Path(__file__).parent.parent.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)
    except ImportError:
        pass

    client_id = os.getenv("SPOTIPY_CLIENT_ID")
    client_secret = os.getenv("SPOTIPY_CLIENT_SECRET")
    redirect_uri = os.getenv("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8765/callback")

    if not client_id or not client_secret:
        print(
            "❌ Spotify API credentials not found.\n"
            "   1. Luo app: https://developer.spotify.com/dashboard\n"
            "   2. Kopioi .env.example → .env projektin juureen\n"
            "   3. Täytä SPOTIPY_CLIENT_ID ja SPOTIPY_CLIENT_SECRET\n",
            file=sys.stderr,
        )
        sys.exit(1)

    return client_id, client_secret, redirect_uri


def get_oauth() -> SpotifyOAuth:
    """Build configured SpotifyOAuth instance.

    Returns:
        SpotifyOAuth: configured for our cache, scopes, and redirect URI
    """
    client_id, client_secret, redirect_uri = _load_credentials()
    return SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=SCOPES,
        cache_path=str(CACHE_FILE),
        open_browser=True,  # Opens default browser for OAuth flow
    )


def get_spotify_client() -> spotipy.Spotify:
    """Get authenticated Spotify client.

    On first call, opens browser for OAuth. On subsequent calls, uses cached
    token (auto-refreshes when expired).

    Returns:
        spotipy.Spotify: authenticated client
    """
    oauth = get_oauth()
    return spotipy.Spotify(auth_manager=oauth)


def is_authenticated() -> bool:
    """Check if we have a valid cached token (no browser needed).

    Returns:
        True if cached token exists and is valid (or refreshable)
    """
    try:
        oauth = get_oauth()
        # Try to get token info without triggering auth flow
        token_info = oauth.cache_handler.get_cached_token()
        return token_info is not None
    except Exception:
        return False


def get_current_user_id() -> Optional[str]:
    """Get the authenticated user's Spotify ID.

    Returns:
        User ID string, or None if not authenticated
    """
    if not is_authenticated():
        return None
    try:
        sp = get_spotify_client()
        user = sp.current_user()
        return user["id"]
    except Exception:
        return None


if __name__ == "__main__":
    # CLI: spotify-curator auth
    print("🎵 Spotify Curator - Authentication\n")
    if is_authenticated():
        user_id = get_current_user_id()
        print(f"✅ Already authenticated as: {user_id}")
        print(f"   Token cache: {CACHE_FILE}")
    else:
        print("🌐 Opening browser for OAuth flow...")
        sp = get_spotify_client()
        user = sp.current_user()
        print(f"✅ Authenticated as: {user['display_name']} ({user['id']})")
        print(f"   Token cache: {CACHE_FILE}")
