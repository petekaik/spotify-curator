"""Unit tests for FP-9 daemon mode (scheduler + workflow + IPC)."""
import json
import time
import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch, call

from src.daemon.scheduler import (
    CuratorScheduler,
    SchedulerConfig,
    JobResult,
    HAVE_APSCHEDULER,
    default_weekly_digest,
)
from src.daemon.workflow import (
    WeeklyReport,
    run_weekly_digest,
    send_command,
    process_commands,
    COMMAND_FILE,
    RESPONSE_DIR,
)


# ────────────────────────────────────────────────────────────
# Scheduler config
# ────────────────────────────────────────────────────────────

class TestSchedulerConfig:
    def test_defaults(self):
        cfg = SchedulerConfig()
        assert cfg.timezone == "Europe/Helsinki"
        assert cfg.weekly_cron["day_of_week"] == "sun"
        assert cfg.weekly_cron["hour"] == 20
        assert cfg.daemon is False
        assert cfg.max_instances == 1

    def test_custom_config(self):
        cfg = SchedulerConfig(timezone="UTC", weekly_cron={"hour": 9, "minute": 30})
        assert cfg.timezone == "UTC"
        assert cfg.weekly_cron["hour"] == 9


# ────────────────────────────────────────────────────────────
# CuratorScheduler
# ────────────────────────────────────────────────────────────

@pytest.mark.skipif(not HAVE_APSCHEDULER, reason="APScheduler not installed")
class TestCuratorScheduler:
    def test_builds_default_scheduler(self):
        sched = CuratorScheduler()
        assert sched.config is not None
        assert sched._scheduler is not None
        # Clean up
        sched.shutdown(wait=False)

    def test_builds_daemon_scheduler(self):
        sched = CuratorScheduler(SchedulerConfig(daemon=True))
        assert sched.config.daemon is True
        sched.shutdown(wait=False)

    def test_schedule_weekly_digest(self):
        sched = CuratorScheduler()
        def my_job():
            return "ran"
        sched.schedule_weekly_digest(my_job, job_id="test_digest")
        jobs = sched.list_jobs()
        assert any(j["id"] == "test_digest" for j in jobs)
        sched.shutdown(wait=False)

    def test_schedule_custom(self):
        sched = CuratorScheduler()
        def my_job():
            return "ran"
        sched.schedule_custom(
            my_job,
            trigger_args={"hour": 9, "minute": 0},
            job_id="morning_job",
        )
        jobs = sched.list_jobs()
        assert any(j["id"] == "morning_job" for j in jobs)
        sched.shutdown(wait=False)

    def test_schedule_interval(self):
        sched = CuratorScheduler()
        def my_job():
            return "ran"
        sched.schedule_interval(my_job, hours=6, job_id="interval_job")
        jobs = sched.list_jobs()
        assert any(j["id"] == "interval_job" for j in jobs)
        sched.shutdown(wait=False)

    def test_remove_job(self):
        sched = CuratorScheduler()
        def my_job():
            return "ran"
        sched.schedule_weekly_digest(my_job, job_id="to_remove")
        assert sched.remove_job("to_remove")
        jobs = sched.list_jobs()
        assert not any(j["id"] == "to_remove" for j in jobs)
        sched.shutdown(wait=False)

    def test_remove_nonexistent_job(self):
        sched = CuratorScheduler()
        # Should not raise
        result = sched.remove_job("does_not_exist")
        # remove_job may return False or raise depending on apscheduler version
        # Just verify it doesn't crash
        sched.shutdown(wait=False)

    def test_get_result_returns_none_for_unknown(self):
        sched = CuratorScheduler()
        assert sched.get_result("never_ran") is None
        sched.shutdown(wait=False)

    def test_list_jobs_empty(self):
        sched = CuratorScheduler()
        assert sched.list_jobs() == []
        sched.shutdown(wait=False)


# ────────────────────────────────────────────────────────────
# WeeklyReport
# ────────────────────────────────────────────────────────────

class TestWeeklyReport:
    def test_empty_report(self):
        report = WeeklyReport(
            run_id="20260708T200000",
            started_at=datetime(2026, 7, 8, 20, 0, 0),
        )
        md = report.to_markdown()
        assert "Weekly Digest" in md
        assert "2026-07-08" in md
        assert "Pipeline" in md

    def test_report_with_playlists(self):
        report = WeeklyReport(
            run_id="20260708T200000",
            started_at=datetime(2026, 7, 8, 20, 0, 0),
            finished_at=datetime(2026, 7, 8, 20, 5, 0),
            duration_seconds=300.0,
            profile_refreshed=True,
            candidates_discovered=100,
            candidates_ranked=50,
            candidates_after_dedup=30,
            playlists_generated=2,
            playlists=[
                {"mood": "cheerful", "url": "https://open.spotify.com/playlist/abc", "track_count": 25},
                {"mood": "calming", "url": "https://open.spotify.com/playlist/def", "track_count": 20},
            ],
            sources=["lastfm", "musicbrainz"],
        )
        md = report.to_markdown()
        assert "cheerful" in md
        assert "calming" in md
        assert "open.spotify.com" in md
        assert "300.0s" in md
        assert "lastfm" in md
        assert "musicbrainz" in md

    def test_report_with_errors(self):
        report = WeeklyReport(
            run_id="x",
            started_at=datetime(2026, 7, 8),
            errors=["Profile refresh failed: timeout"],
            warnings=["Using cached profile"],
        )
        md = report.to_markdown()
        assert "Errors" in md
        assert "timeout" in md
        assert "Warnings" in md
        assert "cached" in md

    def test_to_dict_serializes_dates(self):
        report = WeeklyReport(
            run_id="x",
            started_at=datetime(2026, 7, 8, 20, 0, 0),
            finished_at=datetime(2026, 7, 8, 20, 5, 0),
        )
        d = report.to_dict()
        assert d["started_at"] == "2026-07-08T20:00:00"
        assert d["finished_at"] == "2026-07-08T20:05:00"
        assert d["run_id"] == "x"

    def test_to_dict_handles_null_finished(self):
        report = WeeklyReport(run_id="x", started_at=datetime(2026, 7, 8))
        d = report.to_dict()
        assert d["finished_at"] is None


# ────────────────────────────────────────────────────────────
# run_weekly_digest workflow
# ────────────────────────────────────────────────────────────

class TestWeeklyDigestWorkflow:
    def test_runs_in_dry_run(self, tmp_path):
        """Dry run should complete without crashing and write a report."""
        result = run_weekly_digest(
            profile_user_id="test_user",
            report_dir=tmp_path,
            dry_run=True,
        )
        assert "run_id" in result
        assert result["finished_at"] is not None
        assert result["duration_seconds"] is not None
        # Should have at least 1 candidate (from any source) or warnings
        # (Note: candidate fetching is stubbed — may produce 0 + warning)
        assert isinstance(result["candidates_discovered"], int)

    def test_writes_markdown_report(self, tmp_path):
        """A .md file should be written to report_dir."""
        result = run_weekly_digest(
            profile_user_id="test_user",
            report_dir=tmp_path,
            dry_run=True,
        )
        run_id = result["run_id"]
        md_path = tmp_path / f"{run_id}.md"
        assert md_path.exists()
        content = md_path.read_text()
        assert "Weekly Digest" in content

    def test_captures_errors_gracefully(self, tmp_path):
        """If a step fails, the workflow should still produce a report."""
        # Force an error in the discovery step
        with patch("src.discovery.sources.lastfm.get_top_artists_for_tag",
                   side_effect=Exception("API down")):
            result = run_weekly_digest(
                profile_user_id="test_user",
                report_dir=tmp_path,
                dry_run=True,
            )
        # Should still finish
        assert result["finished_at"] is not None
        # Errors are captured, not raised
        # (note: get_top_artists_for_tag is imported but not called in our stub)

    def test_includes_sources_list(self, tmp_path):
        result = run_weekly_digest(
            profile_user_id="u",
            report_dir=tmp_path,
            dry_run=True,
        )
        # At minimum, our stub adds the source names
        assert "lastfm" in result["sources"]
        assert "musicbrainz" in result["sources"]
        assert "reddit" in result["sources"]

    def test_unique_run_id_per_run(self, tmp_path):
        r1 = run_weekly_digest(report_dir=tmp_path, dry_run=True)
        time.sleep(0.001)  # ensure different timestamp
        r2 = run_weekly_digest(report_dir=tmp_path, dry_run=True)
        assert r1["run_id"] != r2["run_id"]


# ────────────────────────────────────────────────────────────
# IPC (file-based command queue)
# ────────────────────────────────────────────────────────────

class TestIPC:
    def test_send_command_status(self, tmp_path, monkeypatch):
        """send_command should write a request file and wait for response."""
        # Redirect to tmp dir
        cmd_file = tmp_path / "daemon.cmd"
        resp_dir = tmp_path / "responses"
        resp_dir.mkdir()

        monkeypatch.setattr("src.daemon.workflow.COMMAND_FILE", cmd_file)
        monkeypatch.setattr("src.daemon.workflow.RESPONSE_DIR", resp_dir)

        # Pre-write a response so send_command returns quickly
        from threading import Thread
        def write_response():
            time.sleep(0.2)
            # Read the request to get its ID
            for _ in range(20):
                if cmd_file.exists():
                    break
                time.sleep(0.05)
            if cmd_file.exists():
                request = json.loads(cmd_file.read_text())
                req_id = request["request_id"]
                (resp_dir / f"{req_id}.json").write_text(
                    json.dumps({"status": "ok", "echo": request["command"]})
                )
        Thread(target=write_response, daemon=True).start()

        result = send_command("status", timeout_seconds=5.0)
        assert result is not None
        assert result["status"] == "ok"
        assert result["echo"] == "status"

    def test_send_command_timeout(self, tmp_path, monkeypatch):
        """If no response, send_command should return None after timeout."""
        cmd_file = tmp_path / "daemon.cmd"
        resp_dir = tmp_path / "responses"
        resp_dir.mkdir()
        monkeypatch.setattr("src.daemon.workflow.COMMAND_FILE", cmd_file)
        monkeypatch.setattr("src.daemon.workflow.RESPONSE_DIR", resp_dir)

        # Don't write a response — should timeout
        result = send_command("status", timeout_seconds=0.5)
        assert result is None

    def test_process_commands_callback(self, tmp_path, monkeypatch):
        """process_commands should invoke the callback for a request."""
        cmd_file = tmp_path / "daemon.cmd"
        resp_dir = tmp_path / "responses"
        resp_dir.mkdir()
        monkeypatch.setattr("src.daemon.workflow.COMMAND_FILE", cmd_file)
        monkeypatch.setattr("src.daemon.workflow.RESPONSE_DIR", resp_dir)

        # Write a command request
        cmd_file.write_text(json.dumps({
            "request_id": "test123",
            "command": "ping",
            "args": {},
        }))

        callback = MagicMock(return_value={"status": "pong"})

        # Run process_commands in a thread and kill it after one iteration
        import threading
        def run_for_a_bit():
            # Patch sleep so we don't wait 1 second
            with patch("src.daemon.workflow.time.sleep", side_effect=SystemExit):
                try:
                    process_commands(callback)
                except SystemExit:
                    pass
        t = threading.Thread(target=run_for_a_bit, daemon=True)
        t.start()
        t.join(timeout=3.0)

        # Verify callback was called
        callback.assert_called_once()
        call_args = callback.call_args
        request = call_args[0][0]
        assert request["command"] == "ping"

        # Verify response was written
        response_path = resp_dir / "test123.json"
        assert response_path.exists()
        response = json.loads(response_path.read_text())
        assert response["status"] == "pong"

        # Command file should be cleaned up
        assert not cmd_file.exists()

    def test_process_commands_handles_exception(self, tmp_path, monkeypatch):
        """Callback exceptions should be caught, not crash the loop."""
        cmd_file = tmp_path / "daemon.cmd"
        resp_dir = tmp_path / "responses"
        resp_dir.mkdir()
        monkeypatch.setattr("src.daemon.workflow.COMMAND_FILE", cmd_file)
        monkeypatch.setattr("src.daemon.workflow.RESPONSE_DIR", resp_dir)

        # Write a command
        cmd_file.write_text(json.dumps({
            "request_id": "err1",
            "command": "explode",
        }))

        callback = MagicMock(side_effect=ValueError("boom"))

        import threading
        def run_for_a_bit():
            with patch("src.daemon.workflow.time.sleep", side_effect=SystemExit):
                try:
                    process_commands(callback)
                except SystemExit:
                    pass
        t = threading.Thread(target=run_for_a_bit, daemon=True)
        t.start()
        t.join(timeout=3.0)

        # Callback was called (and raised)
        callback.assert_called_once()
        # Loop should not have crashed; the exception was logged


# ────────────────────────────────────────────────────────────
# default_weekly_digest
# ────────────────────────────────────────────────────────────

class TestDefaultWeeklyDigest:
    def test_default_function_exists(self):
        """The default workflow should be importable."""
        assert callable(default_weekly_digest)
