"""Shared runtime config: .env loading and the data directory."""
import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# cwd first (where `spotify-curator init` writes), then project root; neither overrides real env vars.
load_dotenv(Path.cwd() / ".env")
load_dotenv(PROJECT_ROOT / ".env")

# Profiles, caches, token cache, reports, daemon IPC. Container sets SPOTIFY_CURATOR_HOME.
DATA_DIR = Path(os.getenv("SPOTIFY_CURATOR_HOME") or Path.home() / ".spotify-curator")
