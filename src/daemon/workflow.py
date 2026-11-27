"""Weekly digest workflow (FP-9 core).

Composes all the existing FP-1..FP-7 components into a single
discover → rank → playlist pipeline that runs on schedule.

This module is intentionally thin — all the heavy lifting lives in
the underlying modules. The workflow just orchestrates calls and
collects results.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from src.config import DATA_DIR
from src.hermes_skill.tools import (
    VALID_MOODS,
    spotify_curator_generate_mood,
    spotify_curator_refresh_profile,
)

log = logging.getLogger(__name__)


@dataclass
class WeeklyReport:
    """Summary of one weekly digest run."""

    run_id: str  # ISO timestamp + microseconds
    started_at: datetime
    finished_at: datetime | None = None
    duration_seconds: float | None = None

    # Pipeline stats
    profile_refreshed: bool = False
    candidates_discovered: int = 0
    candidates_ranked: int = 0
    candidates_after_dedup: int = 0
    playlists_generated: int = 0
    playlists: list[dict] = field(default_factory=list)  # [{mood, name, url, n_tracks}]

    # Sources used
    sources: list[str] = field(default_factory=list)

    # Errors / warnings
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        """Render the report as Markdown."""
        lines = [
            f"# Weekly Digest — {self.started_at:%Y-%m-%d}",
            "",
            f"**Run ID:** `{self.run_id}`  ",
            f"**Duration:** {self.duration_seconds:.1f}s" if self.duration_seconds else "",
            "",
            "## Pipeline",
            "",
            f"- Profile refreshed: {'✅' if self.profile_refreshed else '❌'}",
            f"- Candidates discovered: {self.candidates_discovered}",
            f"- After ranking: {self.candidates_ranked}",
            f"- After dedup: {self.candidates_after_dedup}",
            f"- Playlists generated: {self.playlists_generated}",
            "",
            "## Sources used",
            "",
        ]
        for src in self.sources:
            lines.append(f"- {src}")
        lines.append("")

        if self.playlists:
            lines.extend(
                [
                    "## Playlists",
                    "",
                ]
            )
            for pl in self.playlists:
                lines.append(
                    f"- **{pl['mood']}**: "
                    f"[Open in Spotify]({pl.get('url') or '#'}) — {pl.get('n_tracks', 0)} tracks"
                )
            lines.append("")

        if self.errors:
            lines.extend(
                [
                    "## Errors",
                    "",
                ]
            )
            for e in self.errors:
                lines.append(f"- {e}")
            lines.append("")

        if self.warnings:
            lines.extend(
                [
                    "## Warnings",
                    "",
                ]
            )
            for w in self.warnings:
                lines.append(f"- {w}")
            lines.append("")

        return "\n".join(lines).strip()

    def to_dict(self) -> dict:
        """Serialize to a dict (for JSON storage)."""
        d = asdict(self)
        d["started_at"] = self.started_at.isoformat()
        d["finished_at"] = self.finished_at.isoformat() if self.finished_at else None
        return d


def run_weekly_digest(
    profile_user_id: str = "default",
    report_dir: Path | None = None,
    dry_run: bool = False,
) -> dict:
    """Run the full weekly digest pipeline.

    Args:
        profile_user_id: which user profile to base recommendations on
        report_dir: where to save the Markdown report (default: DATA_DIR/reports/)
        dry_run: if True, don't write to Spotify (useful for testing)

    Returns:
        dict with pipeline stats (also available as WeeklyReport)
    """
    started = datetime.now()
    run_id = started.strftime("%Y%m%dT%H%M%S%f")
    report = WeeklyReport(
        run_id=run_id,
        started_at=started,
    )

    if report_dir is None:
        report_dir = DATA_DIR / "reports"
    run_dir = report_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    log.info(f"Starting weekly digest run {run_id}")
    log.info(f"Profile: {profile_user_id}, dry_run: {dry_run}")

    try:
        # 1. Refresh the taste profile; fall back to the cached one on failure
        if not dry_run:
            try:
                refreshed = spotify_curator_refresh_profile()
                if "error" in refreshed:
                    report.warnings.append(
                        f"Profile refresh failed: {refreshed['error']} "
                        f"({refreshed.get('details', '')}); using cached profile"
                    )
                else:
                    report.profile_refreshed = True
            except Exception as e:
                report.warnings.append(f"Profile refresh failed: {e}; using cached profile")

        # 2. Build one playlist per mood. A failing mood only adds a warning.
        report.sources.append("lastfm")
        for mood in VALID_MOODS:
            try:
                result = spotify_curator_generate_mood(mood, dry_run=dry_run)
            except Exception as e:
                report.warnings.append(f"Mood {mood} failed: {e}")
                continue
            if "error" in result:
                report.warnings.append(
                    f"Mood {mood} failed: {result['error']} ({result.get('details', '')})"
                )
                continue
            report.playlists.append(
                {
                    "mood": mood,
                    "name": result.get("playlist_name"),
                    "url": result.get("playlist_url"),
                    "n_tracks": result.get("tracks_count", 0),
                }
            )
        report.playlists_generated = len(report.playlists)

        # 3. Save the shared report format + a human-readable report
        report.finished_at = datetime.now()
        report.duration_seconds = (report.finished_at - report.started_at).total_seconds()

        (run_dir / "playlists.json").write_text(
            json.dumps(
                {
                    "id": run_id,
                    "generated_at": started.isoformat(),
                    "dry_run": dry_run,
                    "playlists": report.playlists,
                    "warnings": report.warnings,
                },
                indent=2,
            )
        )
        md_path = run_dir / "report.md"
        md_path.write_text(report.to_markdown())
        log.info(f"Report saved to {md_path}")

        return {**report.to_dict(), "report_path": str(md_path)}

    except Exception as e:
        log.exception(f"Weekly digest run {run_id} failed")
        report.errors.append(f"Fatal: {e}")
        report.finished_at = datetime.now()
        report.duration_seconds = (report.finished_at - report.started_at).total_seconds()
        return report.to_dict()


# ────────────────────────────────────────────────────────────
# IPC (file-based command queue for daemon control)
# ────────────────────────────────────────────────────────────

COMMAND_FILE = DATA_DIR / "daemon.cmd"
RESPONSE_DIR = DATA_DIR / "daemon.responses"


def send_command(
    command: str,
    args: dict | None = None,
    timeout_seconds: float = 30.0,
) -> dict | None:
    """Send a command to the running daemon and wait for response.

    Commands:
    - "status": return scheduler state + last job results
    - "run_now": trigger weekly digest immediately
    - "list_jobs": return all scheduled jobs
    - "stop": gracefully shut down the daemon

    Args:
        command: command name
        args: optional command arguments
        timeout_seconds: how long to wait for response

    Returns:
        dict with response, or None if timeout
    """
    import json
    import uuid
    from datetime import datetime

    if not COMMAND_FILE.parent.exists():
        COMMAND_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not RESPONSE_DIR.exists():
        RESPONSE_DIR.mkdir(parents=True, exist_ok=True)

    request_id = str(uuid.uuid4())
    request = {
        "request_id": request_id,
        "command": command,
        "args": args or {},
        "timestamp": datetime.now().isoformat(),
    }

    # Write request
    COMMAND_FILE.write_text(json.dumps(request))

    # Wait for response
    response_path = RESPONSE_DIR / f"{request_id}.json"
    start = time.time()
    while time.time() - start < timeout_seconds:
        if response_path.exists():
            try:
                return json.loads(response_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass  # half-written by the daemon: keep waiting until timeout
        time.sleep(0.1)
    return None


def process_commands(callback: Callable[[dict], dict]) -> None:
    """Process commands from the command file. Run in a loop.

    Args:
        callback: function that takes a command dict and returns response dict
    """
    import json

    log.info(f"Daemon watching for commands at {COMMAND_FILE}")
    while True:
        if COMMAND_FILE.exists():
            request_id = "unknown"
            try:
                request = json.loads(COMMAND_FILE.read_text())
                request_id = request.get("request_id", "unknown")
                log.info(f"Processing command: {request.get('command')}")
                response = callback(request)
            except Exception as e:
                log.exception(f"Command processing failed: {e}")
                response = {"error": str(e)}
            finally:
                COMMAND_FILE.unlink(missing_ok=True)  # never reprocess a failed command
            RESPONSE_DIR.mkdir(parents=True, exist_ok=True)
            (RESPONSE_DIR / f"{request_id}.json").write_text(json.dumps(response))
        time.sleep(1.0)
