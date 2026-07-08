# Auth shim — run on host Mac, NOT inside container.
#
# Spotify OAuth REQUIRES a browser redirect. Container can't open a browser,
# so we run the OAuth flow locally on Mac, which writes a token cache,
# then the container reads that cache on startup (read-only mount).
#
# This is the standard pattern for headless Spotify integrations.

set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS_DIR="$DEPLOY_DIR/secrets"
CACHE_FILE="$SECRETS_DIR/spotify_cache"
PROJECT_DIR="$(cd "$DEPLOY_DIR/.." && pwd)"

echo "╔════════════════════════════════════════════╗"
echo "║  spotify-curator OAuth flow (host Mac)       ║"
echo "╚════════════════════════════════════════════╝"
echo
echo "This will:"
echo "  1. Open your default browser to Spotify login"
echo "  2. After you approve, write a token cache to:"
echo "     $CACHE_FILE"
echo "  3. That cache is bind-mounted read-only into the container"
echo

# Check .env exists in deploy dir
if [ ! -f "$DEPLOY_DIR/.env" ]; then
    echo "❌ No .env file in $DEPLOY_DIR"
    echo "   Run: cp $DEPLOY_DIR/.env.example $DEPLOY_DIR/.env"
    echo "   Then edit .env with your SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET"
    exit 1
fi

# Load env vars
set -a
. "$DEPLOY_DIR/.env"
set +a

if [ -z "${SPOTIPY_CLIENT_ID:-}" ] || [ -z "${SPOTIPY_CLIENT_SECRET:-}" ]; then
    echo "❌ SPOTIPY_CLIENT_ID or SPOTIPY_CLIENT_SECRET missing in .env"
    exit 1
fi

# Make sure spotify-curator is installed
if [ ! -d "$PROJECT_DIR/src" ]; then
    echo "❌ $PROJECT_DIR/src not found"
    exit 1
fi

# Run auth from project root, but redirect cache file to secrets dir
cd "$PROJECT_DIR"
mkdir -p "$SECRETS_DIR"

# spotipy reads SPOTIPY_CACHE_PATH env var
export SPOTIPY_CACHE_PATH="$CACHE_FILE"

echo "🌐 Opening browser for OAuth..."
echo

# Use the venv python if available, otherwise system
if [ -f "$PROJECT_DIR/.venv/bin/python" ]; then
    PYTHON="$PROJECT_DIR/.venv/bin/python"
else
    PYTHON="python3"
fi

# Direct spotipy call (most reliable, bypasses our CLI surface)
"$PYTHON" -c "
import os
from spotipy.oauth2 import SpotifyOAuth
import json

cache_path = os.environ.get('SPOTIPY_CACHE_PATH', os.path.expanduser('~/.spotify-curator/.cache'))
os.makedirs(os.path.dirname(cache_path), exist_ok=True)

oauth = SpotifyOAuth(
    client_id=os.environ['SPOTIPY_CLIENT_ID'],
    client_secret=os.environ['SPOTIPY_CLIENT_SECRET'],
    redirect_uri=os.environ.get('SPOTIPY_REDIRECT_URI', 'http://127.0.0.1:8765/callback'),
    scope='user-top-read user-library-read user-library-modify playlist-modify-public playlist-modify-private user-follow-read',
    cache_path=cache_path,
    open_browser=True,
)
token_info = oauth.get_access_token(as_dict=True)
print('✓ Got token, expires:', token_info.get('expires_at'))
print('✓ Token cache written to:', cache_path)
"

if [ $? -ne 0 ]; then
    echo "❌ OAuth flow failed"
    exit 1
fi

echo
echo "╔════════════════════════════════════════════╗"
echo "║  ✓ Done! Next steps:                        ║"
echo "╚════════════════════════════════════════════╝"
echo
echo "  1. Verify token: cat $CACHE_FILE | head -3"
echo "  2. Start container: cd $DEPLOY_DIR && docker compose up -d"
echo "  3. Verify auth works: docker compose exec hermes-spotify-curator \\"
echo "       python -c 'import spotipy; from spotipy.oauth2 import SpotifyOAuth; print(\"OK\")'"
echo
