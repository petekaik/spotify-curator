"""Unit tests for FP-9 daemon mode (scheduler + workflow + IPC)."""

import contextlib
import json
import time
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.daemon.scheduler import (
    HAVE_APSCHEDULER,
    CuratorScheduler,
    SchedulerConfig,
    default_weekly_digest,
)
from src.daemon.workflow import (
    WeeklyReport,
    process_commands,
    run_weekly_digest,
    send_command,
)
from src.hermes_skill.tools import VALID_MOODS, spotify_curator_get_reports

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
        sched.remove_job("does_not_exist")
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
            playlists_generated=2,
            playlists=[
                {
                    "mood": "cheerful",
                    "url": "https://open.spotify.com/playlist/abc",
                    "n_tracks": 25,
                },
                {
                    "mood": "calming",
                    "url": "https://open.spotify.com/playlist/def",
                    "n_tracks": 20,
                },
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


def _mood_result(mood, dry_run=True, tracks=3):
    return {
        "success": True,
        "mood": mood,
        "dry_run": dry_run,
        "playlist_name": f"Weekly {mood.capitalize()}",
        "playlist_url": None if dry_run else f"https://open.spotify.com/playlist/{mood}",
        "tracks_count": tracks,
    }


def _read_meta(tmp_path, result):
    return json.loads((tmp_path / result["run_id"] / "playlists.json").read_text())


class TestWeeklyDigestWorkflow:
    def test_dry_run_generates_every_mood_and_writes_report(self, tmp_path):
        """Criterion 1: dry run calls generate_mood per mood, never refreshes."""
        calls = []

        def fake_mood(mood, dry_run=False):
            calls.append((mood, dry_run))
            return _mood_result(mood, dry_run)

        with (
            patch(
                "src.daemon.workflow.spotify_curator_generate_mood",
                side_effect=fake_mood,
            ),
            patch("src.daemon.workflow.spotify_curator_refresh_profile") as refresh,
        ):
            result = run_weekly_digest(report_dir=tmp_path, dry_run=True)

        assert [mood for mood, _ in calls] == list(VALID_MOODS)
        assert all(called_dry_run for _, called_dry_run in calls)
        refresh.assert_not_called()

        meta = _read_meta(tmp_path, result)
        assert meta["dry_run"] is True
        assert len(meta["playlists"]) == 6
        assert result["report_path"] == str(tmp_path / result["run_id"] / "report.md")
        assert (tmp_path / result["run_id"] / "report.md").exists()

    def test_refreshes_profile_before_any_mood(self, tmp_path):
        """Criterion 2: refresh_profile runs exactly once, before generate_mood."""
        order = []

        def fake_refresh():
            order.append("refresh")
            return {"success": True}

        def fake_mood(mood, dry_run=False):
            order.append(mood)
            return _mood_result(mood, dry_run)

        with (
            patch(
                "src.daemon.workflow.spotify_curator_refresh_profile",
                side_effect=fake_refresh,
            ) as refresh,
            patch(
                "src.daemon.workflow.spotify_curator_generate_mood",
                side_effect=fake_mood,
            ),
        ):
            run_weekly_digest(report_dir=tmp_path)

        refresh.assert_called_once()
        assert order[0] == "refresh"
        assert order[1:] == list(VALID_MOODS)

    def test_a_failing_mood_warns_but_does_not_stop_the_run(self, tmp_path):
        """Criterion 3: one erroring mood leaves the other five in the report."""

        def fake_mood(mood, dry_run=False):
            if mood == "focus":
                return {"error": "lastfm_down", "details": "API down"}
            return _mood_result(mood, dry_run)

        with patch(
            "src.daemon.workflow.spotify_curator_generate_mood",
            side_effect=fake_mood,
        ):
            result = run_weekly_digest(report_dir=tmp_path, dry_run=True)

        meta = _read_meta(tmp_path, result)
        assert len(meta["playlists"]) == 5
        assert "focus" not in [p["mood"] for p in meta["playlists"]]
        assert any("lastfm_down" in w for w in result["warnings"])

    def test_a_mood_that_raises_warns_but_does_not_stop_the_run(self, tmp_path):
        """A raised exception is handled like a returned error."""

        def fake_mood(mood, dry_run=False):
            if mood == "focus":
                raise RuntimeError("lastfm exploded")
            return _mood_result(mood, dry_run)

        with patch(
            "src.daemon.workflow.spotify_curator_generate_mood",
            side_effect=fake_mood,
        ):
            result = run_weekly_digest(report_dir=tmp_path, dry_run=True)

        meta = _read_meta(tmp_path, result)
        assert len(meta["playlists"]) == 5
        assert any("lastfm exploded" in w for w in result["warnings"])

    def test_failing_refresh_falls_back_to_cached_profile(self, tmp_path):
        """Criterion 4: refresh failure warns, still runs, never raises."""
        with (
            patch(
                "src.daemon.workflow.spotify_curator_refresh_profile",
                return_value={"error": "profile_build_failed", "details": "no auth"},
            ),
            patch(
                "src.daemon.workflow.spotify_curator_generate_mood",
                side_effect=_mood_result,
            ),
        ):
            result = run_weekly_digest(report_dir=tmp_path)

        assert result["finished_at"] is not None
        assert any("profile" in w.lower() for w in result["warnings"])
        assert result["profile_refreshed"] is False
        assert len(_read_meta(tmp_path, result)["playlists"]) == 6

    def test_refresh_that_raises_falls_back_to_cached_profile(self, tmp_path):
        """A raised refresh exception is a warning, not a failed run."""
        with (
            patch(
                "src.daemon.workflow.spotify_curator_refresh_profile",
                side_effect=RuntimeError("spotify down"),
            ),
            patch(
                "src.daemon.workflow.spotify_curator_generate_mood",
                side_effect=_mood_result,
            ),
        ):
            result = run_weekly_digest(report_dir=tmp_path)

        assert result["finished_at"] is not None
        assert any("spotify down" in w for w in result["warnings"])
        assert len(_read_meta(tmp_path, result)["playlists"]) == 6

    def test_written_run_is_readable_by_get_reports(self, tmp_path, monkeypatch):
        """Criterion 5: get_reports sees the run; mood_filter narrows to one playlist."""
        with patch(
            "src.daemon.workflow.spotify_curator_generate_mood",
            side_effect=_mood_result,
        ):
            run_weekly_digest(report_dir=tmp_path / "reports", dry_run=True)

        monkeypatch.setattr("src.config.DATA_DIR", tmp_path)
        all_runs = spotify_curator_get_reports()
        assert len(all_runs["runs"]) == 1
        assert len(all_runs["runs"][0]["playlists"]) == 6

        focus = spotify_curator_get_reports(mood_filter="focus")
        assert len(focus["runs"]) == 1
        assert [p["mood"] for p in focus["runs"][0]["playlists"]] == ["focus"]

    def test_stub_warnings_are_gone(self, tmp_path):
        """Criterion 7: no 'not implemented' / 'pipeline partial' stub messages."""
        with patch(
            "src.daemon.workflow.spotify_curator_generate_mood",
            side_effect=_mood_result,
        ):
            result = run_weekly_digest(report_dir=tmp_path, dry_run=True)

        warnings = " ".join(result["warnings"]).lower()
        for phrase in ("not yet implemented", "pipeline partial", "skipped in dry_run"):
            assert phrase not in warnings

    def test_unique_run_id_per_run(self, tmp_path):
        with patch(
            "src.daemon.workflow.spotify_curator_generate_mood",
            side_effect=_mood_result,
        ):
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
        cmd_file.write_text(
            json.dumps(
                {
                    "request_id": "test123",
                    "command": "ping",
                    "args": {},
                }
            )
        )

        callback = MagicMock(return_value={"status": "pong"})

        # Run process_commands in a thread and kill it after one iteration
        import threading

        def run_for_a_bit():
            # Patch sleep so we don't wait 1 second
            with patch("src.daemon.workflow.time.sleep", side_effect=SystemExit):
                with contextlib.suppress(SystemExit):
                    process_commands(callback)

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
        cmd_file.write_text(
            json.dumps(
                {
                    "request_id": "err1",
                    "command": "explode",
                }
            )
        )

        callback = MagicMock(side_effect=ValueError("boom"))

        import threading

        def run_for_a_bit():
            with patch("src.daemon.workflow.time.sleep", side_effect=SystemExit):
                with contextlib.suppress(SystemExit):
                    process_commands(callback)

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


def test_send_command_waits_out_a_half_written_response(tmp_path, monkeypatch):
    """A response file seen before its content lands must not end the wait (CI race on 3.13)."""
    import json
    import threading

    from src.daemon.workflow import send_command

    cmd_file, resp_dir = tmp_path / "daemon.cmd", tmp_path / "responses"
    resp_dir.mkdir()
    monkeypatch.setattr("src.daemon.workflow.COMMAND_FILE", cmd_file)
    monkeypatch.setattr("src.daemon.workflow.RESPONSE_DIR", resp_dir)

    def daemon():
        while not cmd_file.exists():
            time.sleep(0.01)
        resp = resp_dir / f"{json.loads(cmd_file.read_text())['request_id']}.json"
        resp.write_text("")  # created, not yet written
        time.sleep(0.3)
        resp.write_text(json.dumps({"status": "ok"}))

    threading.Thread(target=daemon, daemon=True).start()
    assert send_command("status", timeout_seconds=5.0) == {"status": "ok"}
