"""Keep tests away from the real ~/.spotify-curator (profile, token cache, API caches)."""
import os
import tempfile

# Must run before any src module is imported: DATA_DIR is read at import time.
os.environ["SPOTIFY_CURATOR_HOME"] = tempfile.mkdtemp(prefix="spotify-curator-test-")
os.environ.pop("SPOTIPY_CACHE_PATH", None)
