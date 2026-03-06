"""APScheduler-based scheduler service.

Wraps AsyncIOScheduler with SQLAlchemy job store for persistence,
and provides a clean interface for cron / interval jobs.
"""
import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)


class SchedulerService:
    """APScheduler AsyncIOScheduler wrapper with persistent job store."""

    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        jobstore_url = f'sqlite:///{db_path}'

        jobstores = {
            'default': SQLAlchemyJobStore(url=jobstore_url),
        }
        job_defaults = {
            'coalesce': True,
            'max_instances': 1,
            'misfire_grace_time': 3600,
        }
        self._scheduler = AsyncIOScheduler(
            jobstores=jobstores,
            job_defaults=job_defaults,
        )
        self._started = False

    def start(self) -> None:
        """Start the scheduler. Safe to call multiple times."""
        if self._started:
            return
        self._scheduler.start()
        self._started = True
        logger.info('Scheduler started')

    def shutdown(self, wait: bool = True) -> None:
        """Shut down the scheduler."""
        if not self._started:
            return
        self._scheduler.shutdown(wait=wait)
        self._started = False
        logger.info('Scheduler shut down')

    def add_cron_job(self, job_id: str, func, cron_expr: str,
                     kwargs: dict | None = None) -> None:
        """Add or replace a cron-triggered job.

        Args:
            job_id: Unique job identifier.
            func: Callable (async or sync) to execute.
            cron_expr: Standard 5-field cron expression (minute hour day month dow).
            kwargs: Optional keyword arguments to pass to func.
        """
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            raise ValueError(f'Invalid cron expression (need 5 fields): {cron_expr}')

        trigger = CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
        )

        # Remove existing job if any, then add fresh
        self._remove_job_silent(job_id)
        self._scheduler.add_job(
            func, trigger,
            id=job_id,
            name=job_id,
            kwargs=kwargs or {},
            replace_existing=True,
        )
        logger.info('Cron job added: %s [%s]', job_id, cron_expr)

    def add_interval_job(self, job_id: str, func,
                         hours: int = 0, minutes: int = 0, seconds: int = 0,
                         kwargs: dict | None = None) -> None:
        """Add or replace an interval-triggered job."""
        trigger = IntervalTrigger(
            hours=hours, minutes=minutes, seconds=seconds)

        self._remove_job_silent(job_id)
        self._scheduler.add_job(
            func, trigger,
            id=job_id,
            name=job_id,
            kwargs=kwargs or {},
            replace_existing=True,
        )
        logger.info('Interval job added: %s [%dh%dm%ds]',
                     job_id, hours, minutes, seconds)

    def remove_job(self, job_id: str) -> bool:
        """Remove a job by ID. Returns True if removed, False if not found."""
        try:
            self._scheduler.remove_job(job_id)
            logger.info('Job removed: %s', job_id)
            return True
        except Exception:
            return False

    def _remove_job_silent(self, job_id: str) -> None:
        """Remove a job without raising if it doesn't exist."""
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass

    def list_jobs(self) -> list[dict]:
        """Return info about all scheduled jobs."""
        jobs = self._scheduler.get_jobs()
        result = []
        for job in jobs:
            result.append({
                'id': job.id,
                'name': job.name,
                'next_run': str(job.next_run_time) if job.next_run_time else None,
                'trigger': str(job.trigger),
            })
        return result

    def get_job(self, job_id: str) -> dict | None:
        """Get info about a single job, or None if not found."""
        job = self._scheduler.get_job(job_id)
        if not job:
            return None
        return {
            'id': job.id,
            'name': job.name,
            'next_run': str(job.next_run_time) if job.next_run_time else None,
            'trigger': str(job.trigger),
        }
