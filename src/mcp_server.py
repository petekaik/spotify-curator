"""MCP server: spotify-curator's tools for any MCP client (Claude, Codex, OpenClaw, Hermes, ...).

Same functions as the Hermes skill (src/hermes_skill/tools.py), so both interfaces behave alike.

    spotify-curator-mcp                                  # stdio, for local clients
    spotify-curator-mcp --http --host 0.0.0.0 --port 8788  # streamable HTTP at /mcp

HTTP on anything other than localhost requires SPOTIFY_CURATOR_MCP_TOKEN; clients then send
`Authorization: Bearer <token>`. GET /health is open for container health checks.
"""

from __future__ import annotations

import argparse
import hmac
import os
from importlib.metadata import PackageNotFoundError, version
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.hermes_skill import tools as t

try:
    VERSION = version("spotify-curator")
except PackageNotFoundError:  # running from a checkout without install
    VERSION = "0.0.0"

TOKEN_ENV = "SPOTIFY_CURATOR_MCP_TOKEN"  # secretscan:ignore (env var name)
LOOPBACK = {"127.0.0.1", "localhost", "::1"}

Mood = Literal["cheerful", "calming", "stimulating", "focus", "melancholic", "energetic"]
assert set(Mood.__args__) == set(t.VALID_MOODS), "Mood literal out of sync with VALID_MOODS"

# Hints let clients decide what needs the user's confirmation.
READS = ToolAnnotations(read_only_hint=True, open_world_hint=True)
WRITES_SPOTIFY = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)
LOCAL_STATE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)

server = MCPServer(
    name="spotify-curator",
    version=VERSION,
    website_url="https://github.com/petekaik/spotify-curator",
    instructions=(
        "Finds small and emerging artists that fit the user's Spotify taste and builds playlists. "
        "Start with get_status. Use discover or create_mood_playlist with dry_run=true to preview, "
        "and get_tuning / update_tuning to change how ranking behaves; tuning is saved and applies "
        "to every later run (CLI, Hermes and MCP)."
    ),
)


def _ok(result: dict[str, Any]) -> dict[str, Any]:
    """The shared tools return {'error': ...} instead of raising; surface that as an MCP error."""
    if "error" in result:
        raise ToolError(f"{result['error']}: {result.get('details', '')}".rstrip(": "))
    return result


@server.tool(annotations=READS)
def get_status() -> dict[str, Any]:
    """Spotify authentication, taste-profile age and Last.fm configuration."""
    return _ok(t.spotify_curator_status())


@server.tool(annotations=LOCAL_STATE)
def refresh_profile(
    include_audio_features: Annotated[
        bool, Field(description="Also fetch Spotify audio features (deprecated, often empty).")
    ] = False,
) -> dict[str, Any]:
    """Rebuild the taste profile from the user's Spotify top artists and tracks (~30-60 s)."""
    return _ok(t.spotify_curator_refresh_profile(include_audio_features=include_audio_features))


@server.tool(annotations=READS)
def discover(
    tag: Annotated[
        str, Field(description="Last.fm tag, e.g. 'stoner rock', 'shoegaze'.")
    ] = "indie rock",
    period: Literal["overall", "7day", "1month", "3month", "6month", "12month"] = "6month",
    limit: Annotated[int, Field(ge=1, le=100)] = 30,
) -> dict[str, Any]:
    """Find and rank emerging artists for a tag against the taste profile. Never writes to Spotify."""
    return _ok(t.spotify_curator_discover(tag=tag, period=period, limit=limit))


@server.tool(annotations=WRITES_SPOTIFY)
def create_mood_playlist(
    mood: Mood,
    n_artists: Annotated[int, Field(ge=1, le=50)] = 15,
    tracks_per_artist: Annotated[int, Field(ge=1, le=5)] = 2,
    name: Annotated[
        str | None, Field(description="Playlist name; default 'Weekly <Mood> — <week>'.")
    ] = None,
    public: bool = False,
    dry_run: Annotated[
        bool, Field(description="Preview the tracks without writing to Spotify.")
    ] = False,
) -> dict[str, Any]:
    """Build a playlist for one mood (seeded by the tuned mood tag) and write it to Spotify."""
    return _ok(
        t.spotify_curator_generate_mood(
            mood=mood,
            n_artists=n_artists,
            tracks_per_artist=tracks_per_artist,
            name=name,
            public=public,
            dry_run=dry_run,
        )
    )


@server.tool(annotations=WRITES_SPOTIFY)
def create_weekly_playlists(
    n_artists_per_mood: Annotated[int, Field(ge=1, le=50)] = 15,
    tracks_per_artist: Annotated[int, Field(ge=1, le=5)] = 2,
    public: bool = False,
    refresh_profile_first: bool = False,
) -> dict[str, Any]:
    """Write one playlist per mood (six in total) to Spotify."""
    return _ok(
        t.spotify_curator_generate_weekly(
            n_artists_per_mood=n_artists_per_mood,
            tracks_per_artist=tracks_per_artist,
            public=public,
            refresh_profile_first=refresh_profile_first,
        )
    )


@server.tool(annotations=READS)
def get_reports(
    limit: Annotated[int, Field(ge=1, le=100)] = 10,
    mood_filter: Mood | None = None,
) -> dict[str, Any]:
    """Past weekly runs and their playlist URLs."""
    return _ok(t.spotify_curator_get_reports(limit=limit, mood_filter=mood_filter))


@server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
def get_tuning() -> dict[str, Any]:
    """Current ranking tuning, the defaults, and what each setting does."""
    return _ok(t.spotify_curator_get_tuning())


@server.tool(annotations=LOCAL_STATE)
def update_tuning(
    changes: Annotated[
        dict[str, Any] | None,
        Field(
            description=(
                "Settings to change; omitted ones keep their value. Example: "
                '{"mainstream_penalty": -0.5, "preferred_geo_tags": ["finnish"], '
                '"mood_tags": {"focus": "post-rock"}}. See get_tuning for all keys.'
            )
        ),
    ] = None,
    reset: Annotated[
        bool, Field(description="Restore all defaults before applying changes.")
    ] = False,
) -> dict[str, Any]:
    """Change how artists are ranked and which tag seeds each mood. Saved; applies to later runs."""
    return _ok(t.spotify_curator_update_tuning(changes=changes, reset=reset))


@server.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "version": VERSION})


class BearerAuth:
    """ASGI middleware: every HTTP request except /health must carry the shared token."""

    def __init__(self, app, token: str):
        self.app, self.expected = app, f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] != "/health":
            got = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(got, self.expected):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def http_app(host: str, token: str | None):
    """Streamable-HTTP app at /mcp. Refuses to expose an unauthenticated server off localhost."""
    if not token and host not in LOOPBACK:
        raise SystemExit(f"Refusing to serve on {host} without {TOKEN_ENV} set.")
    # No token: the SDK's loopback defaults (allow 127.0.0.1/localhost, block DNS rebinding).
    # With a token, rebinding protection adds nothing and would reject LAN/NAS hostnames.
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False) if token else None
    app = server.streamable_http_app(host=host, transport_security=security)
    return BearerAuth(app, token) if token else app


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="spotify-curator-mcp", description=__doc__.split("\n")[0])
    p.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    p.add_argument("--host", default=os.getenv("SPOTIFY_CURATOR_MCP_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.getenv("SPOTIFY_CURATOR_MCP_PORT", "8788")))
    args = p.parse_args(argv)
    if not args.http:
        server.run("stdio")
        return
    import uvicorn

    uvicorn.run(http_app(args.host, os.getenv(TOKEN_ENV)), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
