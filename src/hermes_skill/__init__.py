"""Hermes skill package for spotify-curator.

This package is loaded as a skill by Hermes Agent when the
spotify-curator skill is preloaded (via `HERMES_PRELOAD_SKILLS=spotify-curator`
or `--skills=spotify-curator`).

The actual tool registration happens in `src/hermes_skill/tools.py` via
the `TOOL_REGISTRY` dict. When the skill is loaded, the loader imports
this package and calls `register_tools()` to add each tool to Hermes's
toolset.
"""
from .tools import (
    spotify_curator_status,
    spotify_curator_refresh_profile,
    spotify_curator_discover,
    spotify_curator_generate_mood,
    spotify_curator_generate_weekly,
    spotify_curator_get_reports,
    TOOL_REGISTRY,
)


def register_tools(hermes_tools_registry) -> None:
    """Register all spotify-curator tools with the Hermes tools registry.

    Args:
        hermes_tools_registry: the registry object from Hermes Agent
            (passed in by the skill loader, type depends on Hermes internals)
    """
    for name, spec in TOOL_REGISTRY.items():
        hermes_tools_registry.register(
            name=name,
            toolset="spotify-curator",
            schema=spec["schema"],
            handler=spec["function"],
        )


__all__ = [
    "spotify_curator_status",
    "spotify_curator_refresh_profile",
    "spotify_curator_discover",
    "spotify_curator_generate_mood",
    "spotify_curator_generate_weekly",
    "spotify_curator_get_reports",
    "TOOL_REGISTRY",
    "register_tools",
]
