"""Spotify API authentication wrapper.

Handles the OAuth authorization-code flow for the Spotify Web API.
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

import spotipy
from spotipy.oauth2 import SpotifyOAuth

from src.config import DATA_DIR

# OAuth token cache. Container points SPOTIPY_CACHE_PATH at the host-made token (deploy/scripts/auth_on_host.sh).
CACHE_FILE = Path(os.getenv("SPOTIPY_CACHE_PATH") or DATA_DIR / ".cache")
OPEN_BROWSER = os.getenv("SPOTIPY_OPEN_BROWSER", "true").lower() != "false"

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
    client_id = os.getenv("SPOTIPY_CLIENT_ID")
    client_secret = os.getenv("SPOTIPY_CLIENT_SECRET")  # secretscan:ignore (env lookup, no value)
    redirect_uri = os.getenv("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:43827/spotify/callback")

    if (
        not client_id
        or not client_secret
        or client_id.lower().startswith("your_")
        or client_secret.lower().startswith("your_")
    ):
        print(
            "❌ Spotify API credentials are still placeholders.\n"
            "   1. Create or open your Spotify app: https://developer.spotify.com/dashboard\n"
            "   2. Copy the real client ID and client secret into .env\n"
            f"   3. Register this redirect URI on the app: {redirect_uri}\n",
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
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    return SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,  # secretscan:ignore
        redirect_uri=redirect_uri,
        scope=SCOPES,
        cache_path=str(CACHE_FILE),
        open_browser=OPEN_BROWSER,
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
        token_info = oauth.cache_handler.get_cached_token()  # secretscan:ignore
        return token_info is not None
    except Exception:
        return False


def get_current_user_id() -> str | None:
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
