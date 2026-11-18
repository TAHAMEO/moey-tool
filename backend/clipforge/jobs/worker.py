"""Durable worker process.

Run one or more workers per queue. ``default`` workers hold the master key
(publishing, OAuth, paid APIs, orchestration); ``media`` workers process
untrusted media and are deployed without the master key or network access to
anything but the database, Redis and object storage.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
import traceback
import uuid
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from ..config import get_settings
from ..db.base import session_scope, utcnow
from ..db.models import Job, Schedule, WorkerHeartbeat
from ..security.crypto import get_box
from ..services import control, redis_client
from . import queue
from .registry import (
    HANDLERS,
    Cancelled,
    JobContext,
    PauseRequested,
    PermanentError,
    RetryableError,
    Suspend,
)

log = logging.getLogger("clipforge.worker")

DEFAULT_SCHEDULES = [
    ("analytics-collect", "analytics.collect_due", 15 * 60),
    ("learning-run", "learning.run", 6 * 3600),
    ("maintenance-cleanup", "maintenance.cleanup", 6 * 3600),
    ("oauth-refresh", "maintenance.refresh_tokens", 3600),
    ("publish-scheduled", "maintenance.publish_scheduled", 5 * 60),
    ("backup-daily", "maintenance.backup", 24 * 3600),
]

TRANSIENT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    ConnectionError, TimeoutError, httpx.TransportError, OperationalError, RetryableError,
)


def _is_transient(exc: BaseException) -> bool:
    from ..providers.base import ProviderError

    if isinstance(exc, ProviderError):
        return exc.retryable
    return isinstance(exc, TRANSIENT_EXCEPTIONS)


def seed_schedules() -> None:
    """Idempotent and race-free: several workers may start at the same moment."""
    from sqlalchemy.dialects.postgresql import insert

    with session_scope() as db:
        for name, job_type, interval in DEFAULT_SCHEDULES:
            db.execute(insert(Schedule).values(name=name, job_type=job_type, interval_seconds=interval, payload={},
                                               enabled=True, next_run_at=utcnow() + timedelta(seconds=60))
                       .on_conflict_do_nothing(index_elements=["name"]))


def run_due_schedules() -> int:
    created = 0
    with session_scope() as db:
        due = db.execute(
            select(Schedule).where(Schedule.enabled.is_(True), Schedule.next_run_at <= utcnow())
            .with_for_update(skip_locked=True)
        ).scalars().all()
        for schedule in due:
            if schedule.job_type in HANDLERS:
                queue.enqueue(db, schedule.job_type, schedule.payload, queue=HANDLERS[schedule.job_type].queue,
                              dedupe_key=f"schedule:{schedule.name}", priority=200, max_attempts=2)
                created += 1
            schedule.last_run_at = utcnow()
            schedule.next_run_at = utcnow() + timedelta(seconds=schedule.interval_seconds)
    return created


class Worker:
    def __init__(self, queues: list[str], *, worker_id: str | None = None):
        settings = get_settings()
        self.queues = queues
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self.lease = settings.job_lease_seconds
        self.has_secrets = get_box().available
        self._stop = threading.Event()
        self._current: JobContext | None = None
        self._current_lock = threading.Lock()
        self._last_maintenance = 0.0

    # ------------------------------------------------------------ lifecycle
    def stop(self) -> None:
        self._stop.set()

    @staticmethod
    def _capabilities() -> dict:
        try:
            from ..media.transcription import engines_status

            return {"transcription": [e["engine"] for e in engines_status() if e["available"]]}
        except Exception:  # noqa: BLE001 - pragma: no cover - diagnostics only, never blocks the worker
            return {}

    def _register(self) -> None:
        capabilities = self._capabilities()
        with session_scope() as db:
            row = db.get(WorkerHeartbeat, self.worker_id)
            if row is None:
                db.add(WorkerHeartbeat(worker_id=self.worker_id, queues=",".join(self.queues),
                                       hostname=socket.gethostname(), pid=os.getpid(),
                                       has_secrets=self.has_secrets, capabilities=capabilities))

    def _heartbeat_loop(self) -> None:
        beats = 0
        while not self._stop.wait(10):
            beats += 1
            try:
                # Models can be installed while the worker runs: refresh what it reports every ~5 min.
                capabilities = self._capabilities() if beats % 30 == 0 else None
                with session_scope() as db:
                    row = db.get(WorkerHeartbeat, self.worker_id)
                    if row is not None:
                        row.seen_at = utcnow()
                        if capabilities is not None:
                            row.capabilities = capabilities
                with self._current_lock:
                    ctx = self._current
                if ctx is not None:
                    flags = queue.heartbeat(ctx.job_id, self.worker_id, self.lease)
                    ctx.flags.update(flags)
            except Exception as exc:  # noqa: BLE001 - keep beating through transient DB issues
                log.warning("Heartbeat error: %s", exc)

    def _maintenance(self) -> None:
        now = time.monotonic()
        if now - self._last_maintenance < 20:
            return
        self._last_maintenance = now
        try:
            queue.recover_stale()
            if "default" in self.queues:
                run_due_schedules()
        except Exception as exc:  # noqa: BLE001 - degrade gracefully; logged
            log.warning("Maintenance error: %s", exc)

    def run(self, *, max_jobs: int | None = None, idle_exit_s: float | None = None) -> int:
        """Process jobs until stopped. Returns the number of jobs handled."""
        self._register()
        if "default" in self.queues:
            seed_schedules()
        beat = threading.Thread(target=self._heartbeat_loop, name="heartbeat", daemon=True)
        beat.start()
        handled = 0
        idle_since = time.monotonic()
        log.info("Worker %s serving queues %s (secrets=%s)", self.worker_id, self.queues, self.has_secrets)
        try:
            while not self._stop.is_set():
                self._maintenance()
                with session_scope() as db:
                    paused = control.is_paused(db)
                job = queue.claim(self.worker_id, self.queues, self.lease, paused=paused)
                if job is None:
                    if idle_exit_s is not None and time.monotonic() - idle_since > idle_exit_s:
                        break
                    redis_client.wait_signal([f"cf:queue:{q}" for q in self.queues], 2 if idle_exit_s else 5)
                    continue
                self._execute(job)
                handled += 1
                idle_since = time.monotonic()
                if max_jobs is not None and handled >= max_jobs:
                    break
        finally:
            self._stop.set()
            with session_scope() as db:
                row = db.get(WorkerHeartbeat, self.worker_id)
                if row is not None:
                    db.delete(row)
        return handled

    # ------------------------------------------------------------ execution
    def _execute(self, job: Job) -> None:
        spec = HANDLERS.get(job.type)
        if spec is None:
            queue.fail(job.id, {"message": f"No handler registered for job type {job.type}"}, retryable=False)
            return
        if spec.needs_secrets and not self.has_secrets:
            # Mis-routed: give it back for a worker that holds the master key.
            queue.fail(job.id, {"message": "Worker without secrets cannot run this job"}, retryable=True)
            return
        ctx = JobContext(
            job_id=job.id, type=job.type, payload=dict(job.payload or {}), attempt=job.attempts,
            checkpoint=dict(job.checkpoint or {}), resume_value=job.resume_value, project_id=job.project_id,
            user_id=job.user_id, worker_id=self.worker_id,
        )
        with self._current_lock:
            self._current = ctx
        started = time.monotonic()
        try:
            result = spec.fn(ctx) or {}
            label = result.pop("_label", "Completed") if isinstance(result, dict) else "Completed"
            queue.complete(job.id, result, label=label)
        except Suspend as suspend:
            queue.mark_waiting(job.id, label=suspend.label, info=suspend.info)
        except PauseRequested:
            queue.mark_paused(job.id)
        except Cancelled:
            queue.mark_cancelled(job.id, "Cancelled by user")
        except PermanentError as exc:
            queue.fail(job.id, {"message": exc.user_message, "code": exc.code, **exc.details}, retryable=False,
                       label=exc.label)
        except Exception as exc:
            transient = _is_transient(exc)
            log.exception("Job %s (%s) failed%s", job.id, job.type, " (transient)" if transient else "")
            queue.fail(
                job.id,
                {"message": f"{exc.__class__.__name__}: {str(exc)[:800]}", "code": "exception",
                 "trace": traceback.format_exc(limit=6)[-3000:]},
                retryable=transient,
            )
        finally:
            with self._current_lock:
                self._current = None
            log.info("Job %s (%s) finished in %.1fs", job.id, job.type, time.monotonic() - started)


def run_worker(queues: list[str]) -> None:
    import signal

    from .. import handlers  # noqa: F401  (registers job handlers)

    worker = Worker(queues)
    signal.signal(signal.SIGTERM, lambda *_: worker.stop())
    signal.signal(signal.SIGINT, lambda *_: worker.stop())
    worker.run()
