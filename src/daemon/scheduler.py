"""Scheduler for periodic discovery + playlist generation (FP-9).

Runs the discover → rank → playlist pipeline on a cron schedule.
Default: weekly digest on Sundays at 20:00 local time.

Why APScheduler:
- Pure Python, no external cron daemon needed
- Persistent jobs (survive restarts) via SQLAlchemyJobStore
- Per-job timezone support (handles DST automatically)
- Built-in catch-up if the system was down at scheduled time

Usage:
    >>> from src.daemon.scheduler import CuratorScheduler
    >>> sched = CuratorScheduler()
    >>> sched.start()  # blocks; runs jobs in background thread
    >>> # or schedule.add_job(...) to add custom jobs
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

# APScheduler is optional; provide a clear error if missing
try:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
    HAVE_APSCHEDULER = True
except ImportError:
    HAVE_APSCHEDULER = False
    log.warning("APScheduler not installed; FP-9 daemon mode unavailable")


DEFAULT_CRON = {
    "day_of_week": "sun",   # Sunday
    "hour": 20,             # 8 PM
    "minute": 0,
}


@dataclass
class JobResult:
    """Result of a single scheduled job execution."""
    job_id: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    success: bool = False
    error: Optional[str] = None
    output: Any = None  # job-specific output (e.g. generated playlist ID)


@dataclass
class SchedulerConfig:
    """Configuration for the daemon scheduler."""
    timezone: str = "Europe/Helsinki"  # Pomo's TZ (EET)
    jobstore_url: Optional[str] = None  # e.g. "sqlite:///jobs.db" for persistence
    weekly_cron: dict = field(default_factory=lambda: dict(DEFAULT_CRON))
    daemon: bool = False  # True = background thread, False = blocking
    max_instances: int = 1  # one job at a time
    coalesce: bool = True  # combine missed runs into one


class CuratorScheduler:
    """High-level wrapper around APScheduler with our domain defaults.

    Design choices:
    - Default schedule: weekly on Sunday 20:00 (Pomo's typical playlist refresh)
    - Job results are kept in memory (not persisted) for quick CLI access
    - Errors are caught and logged; jobs don't crash the scheduler
    """

    def __init__(self, config: Optional[SchedulerConfig] = None):
        if not HAVE_APSCHEDULER:
            raise RuntimeError(
                "APScheduler not installed. Run: pip install apscheduler>=3.10.0"
            )
        self.config = config or SchedulerConfig()
        self._results: dict[str, JobResult] = {}
        self._scheduler = self._build_scheduler()

    def _build_scheduler(self):
        """Build APScheduler with our config."""
        scheduler_cls = (
            BackgroundScheduler if self.config.daemon
            else BlockingScheduler
        )
        kwargs: dict[str, Any] = {
            "timezone": self.config.timezone,
        }
        if self.config.jobstore_url:
            from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
            kwargs["jobstores"] = {"default": SQLAlchemyJobStore(url=self.config.jobstore_url)}
        sched = scheduler_cls(**kwargs)
        sched.add_listener(
            self._on_job_event,
            EVENT_JOB_EXECUTED | EVENT_JOB_ERROR,
        )
        return sched

    def _on_job_event(self, event) -> None:
        """Handle job completion/error events."""
        job_id = event.job_id
        if event.exception:
            log.error(f"Job {job_id} failed: {event.exception}")
            self._results[job_id] = JobResult(
                job_id=job_id,
                started_at=datetime.now(),
                finished_at=datetime.now(),
                success=False,
                error=str(event.exception),
            )
        else:
            log.info(f"Job {job_id} completed successfully")
            self._results[job_id] = JobResult(
                job_id=job_id,
                started_at=datetime.now(),
                finished_at=datetime.now(),
                success=True,
            )

    def schedule_weekly_digest(
        self,
        func: Callable[..., Any],
        job_id: str = "weekly_digest",
        replace: bool = True,
    ) -> None:
        """Schedule a job to run weekly on the configured cron.

        Args:
            func: callable to execute (must accept no positional args)
            job_id: unique identifier for this job
            replace: if True, replace existing job with same ID
        """
        trigger = CronTrigger(**self.config.weekly_cron, timezone=self.config.timezone)
        self._scheduler.add_job(
            func,
            trigger=trigger,
            id=job_id,
            replace_existing=replace,
            max_instances=self.config.max_instances,
            coalesce=self.config.coalesce,
        )
        log.info(f"Scheduled '{job_id}' with cron: {self.config.weekly_cron}")

    def schedule_custom(
        self,
        func: Callable[..., Any],
        trigger_args: dict,
        job_id: str,
        replace: bool = True,
    ) -> None:
        """Schedule a job with custom cron args.

        Args:
            func: callable to execute
            trigger_args: CronTrigger kwargs (day_of_week, hour, minute, etc.)
            job_id: unique identifier
            replace: if True, replace existing
        """
        trigger = CronTrigger(**trigger_args, timezone=self.config.timezone)
        self._scheduler.add_job(
            func,
            trigger=trigger,
            id=job_id,
            replace_existing=replace,
            max_instances=self.config.max_instances,
        )
        log.info(f"Scheduled '{job_id}' with custom cron: {trigger_args}")

    def schedule_interval(
        self,
        func: Callable[..., Any],
        hours: int = 0,
        minutes: int = 0,
        seconds: int = 0,
        job_id: str = "interval_job",
        replace: bool = True,
    ) -> None:
        """Schedule a job to run at fixed intervals.

        Useful for: profile refresh every 6h, cache cleanup daily, etc.
        """
        from apscheduler.triggers.interval import IntervalTrigger
        trigger = IntervalTrigger(
            hours=hours, minutes=minutes, seconds=seconds,
            timezone=self.config.timezone,
        )
        self._scheduler.add_job(
            func, trigger=trigger, id=job_id, replace_existing=replace,
        )
        log.info(f"Scheduled '{job_id}' every {hours}h{minutes}m{seconds}s")

    def list_jobs(self) -> list[dict]:
        """List all scheduled jobs."""
        jobs = []
        for job in self._scheduler.get_jobs():
            try:
                next_run = str(job.next_run_time) if job.next_run_time else None
            except (AttributeError, TypeError):
                # next_run_time only available when scheduler is running
                next_run = None
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": next_run,
                "trigger": str(job.trigger),
            })
        return jobs

    def get_result(self, job_id: str) -> Optional[JobResult]:
        """Get the most recent result for a job."""
        return self._results.get(job_id)

    def get_all_results(self) -> list[JobResult]:
        """Get all stored results."""
        return list(self._results.values())

    def remove_job(self, job_id: str) -> bool:
        """Remove a scheduled job. Returns True if removed."""
        try:
            self._scheduler.remove_job(job_id)
            return True
        except Exception:
            return False

    def start(self) -> None:
        """Start the scheduler. Blocks if not daemon mode."""
        log.info(f"Starting scheduler (daemon={self.config.daemon})")
        if not self._scheduler.get_jobs():
            log.warning("No jobs scheduled. Add jobs before calling start().")
        self._scheduler.start()

    def shutdown(self, wait: bool = True) -> None:
        """Shut down the scheduler."""
        from apscheduler.schedulers.base import STATE_RUNNING, STATE_STOPPED
        log.info("Shutting down scheduler")
        # Only shutdown if running; otherwise it's a no-op
        if self._scheduler.state == STATE_RUNNING:
            self._scheduler.shutdown(wait=wait)


# ────────────────────────────────────────────────────────────
# Default weekly digest workflow
# ────────────────────────────────────────────────────────────

def default_weekly_digest() -> dict:
    """Default weekly digest workflow.

    Runs the full pipeline:
    1. Refresh user profile (top artists/tracks from last 6 months)
    2. Discover candidates from all sources (Last.fm, ListenBrainz, MusicBrainz, Reddit)
    3. Rank candidates against profile
    4. Generate 6 mood playlists
    5. Save weekly report

    Returns:
        dict with keys: profile_refreshed, candidates_count, playlists_generated, report_path
    """
    from src.daemon.workflow import run_weekly_digest
    return run_weekly_digest()
