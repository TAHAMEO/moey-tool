"""Durable job queue on PostgreSQL.

* Claims use ``SELECT … FOR UPDATE SKIP LOCKED`` so any number of workers can
  compete safely.
* A claimed job holds a time-limited lease renewed by heartbeats. If a worker
  dies, the lease expires and the job is re-queued; handlers resume from their
  saved checkpoint (LangGraph checkpoints for workflows, per-chunk records
  for media work), so nothing restarts from scratch.
* Failures are retried with exponential backoff up to ``max_attempts``; failed
  jobs are never silently discarded — they stay inspectable and retryable.
* Parent/child: a workflow waits (status ``waiting``) on children grouped by
  ``wait_group``; when every child in the group is terminal the parent is
  re-queued with the children's outcomes as its ``resume_value``.

Redis only carries wake-up signals; without it workers poll.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from ..db.base import session_scope, utcnow
from ..db.models import Job, JobEvent, Project
from ..services import redis_client

log = logging.getLogger(__name__)

ACTIVE = ("queued", "running", "waiting", "paused")
TERMINAL = ("completed", "failed", "cancelled")
SYSTEM_TYPES_IGNORING_PAUSE = ("maintenance.", "analytics.", "learning.")


def _signal(queue: str) -> None:
    redis_client.publish(f"cf:queue:{queue}", "1")


def _progress_event(job: Job) -> None:
    redis_client.publish(
        "cf:jobs",
        json.dumps({"id": str(job.id), "status": job.status, "label": job.status_label, "progress": job.progress,
                    "project_id": str(job.project_id) if job.project_id else None}),
    )


# ---------------------------------------------------------------- enqueue
def enqueue(
    db: Session,
    job_type: str,
    payload: dict[str, Any] | None = None,
    *,
    queue: str = "default",
    priority: int = 100,
    dedupe_key: str | None = None,
    parent_id: uuid.UUID | None = None,
    wait_group: str | None = None,
    project_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    run_after=None,
    max_attempts: int = 3,
    status_label: str = "Queued",
) -> Job:
    """Create a job (or return the active job with the same ``dedupe_key``)."""
    if dedupe_key:
        existing = db.execute(
            select(Job).where(Job.dedupe_key == dedupe_key, Job.status.in_(ACTIVE))
        ).scalar_one_or_none()
        if existing is not None:
            return existing
    job = Job(
        type=job_type,
        queue=queue,
        payload=payload or {},
        priority=priority,
        dedupe_key=dedupe_key,
        parent_id=parent_id,
        wait_group=wait_group,
        project_id=project_id,
        user_id=user_id,
        run_after=run_after or utcnow(),
        max_attempts=max_attempts,
        status_label=status_label,
    )
    db.add(job)
    db.flush()
    db.add(JobEvent(job_id=job.id, message=f"Queued {job_type}", stage="queued"))
    _signal(queue)
    return job


def ensure_child(db: Session, parent: Job | uuid.UUID, key: str, job_type: str, payload: dict[str, Any], *,
                 queue: str = "default", wait_group: str, max_attempts: int = 3, priority: int = 90,
                 status_label: str = "Queued") -> Job:
    """Idempotently create the child identified by ``key`` under ``parent``.

    Re-executing a workflow node (which LangGraph does on resume) must not
    spawn duplicate children, so children are looked up by (parent, key).
    """
    parent_id = parent.id if isinstance(parent, Job) else parent
    parent_row = db.get(Job, parent_id)
    existing = db.execute(
        select(Job).where(Job.parent_id == parent_id, Job.payload["child_key"].astext == key)
    ).scalars().first()
    if existing is not None:
        return existing
    return enqueue(
        db, job_type, {**payload, "child_key": key}, queue=queue, parent_id=parent_id, wait_group=wait_group,
        project_id=parent_row.project_id if parent_row else None,
        user_id=parent_row.user_id if parent_row else None, max_attempts=max_attempts, priority=priority,
        status_label=status_label,
    )


# ------------------------------------------------------------------ claim
def claim(worker_id: str, queues: list[str], lease_seconds: int, *, paused: bool = False) -> Job | None:
    with session_scope() as db:
        now = utcnow()
        query = (
            select(Job)
            .where(Job.queue.in_(queues), Job.status == "queued", Job.run_after <= now)
            .order_by(Job.priority, Job.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if paused:
            query = query.where(or_(*[Job.type.startswith(p) for p in SYSTEM_TYPES_IGNORING_PAUSE]))
        job = db.execute(query).scalar_one_or_none()
        if job is None:
            return None
        job.status = "running"
        job.locked_by = worker_id
        job.attempts += 1
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.heartbeat_at = now
        job.started_at = job.started_at or now
        if job.status_label in ("Queued", "Retrying"):
            job.status_label = "Starting"
        db.add(JobEvent(job_id=job.id, message=f"Started by {worker_id} (attempt {job.attempts})", stage="running"))
        db.flush()
        db.expunge(job)
        return job


def heartbeat(job_id: uuid.UUID, worker_id: str, lease_seconds: int) -> dict[str, bool]:
    """Renew the lease. Returns flags telling the worker to stop."""
    with session_scope() as db:
        job = db.get(Job, job_id)
        if job is None or job.locked_by != worker_id or job.status != "running":
            return {"lost": True, "cancel": True, "pause": False}
        job.heartbeat_at = utcnow()
        job.lease_expires_at = job.heartbeat_at + timedelta(seconds=lease_seconds)
        return {"lost": False, "cancel": job.cancel_requested,
                "pause": bool((job.checkpoint or {}).get("pause_requested"))}


# --------------------------------------------------------------- progress
def set_progress(job_id: uuid.UUID, *, progress: float | None = None, label: str | None = None,
                 stage: str | None = None, message: str | None = None, data: dict | None = None,
                 detail: dict | None = None) -> None:
    """``detail``: live figures for the step that is running (for a download: bytes done and in
    total, speed, time left), shown on the job card and replaced by each update."""
    with session_scope() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        if progress is not None:
            job.progress = max(0.0, min(1.0, float(progress)))
        if detail is not None:
            job.checkpoint = {**(job.checkpoint or {}), "detail": detail}
        if label:
            job.status_label = label
        if stage:
            job.stage = stage
        if message:
            db.add(JobEvent(job_id=job_id, message=message, stage=stage or job.stage, data=data or {}))
        _progress_event(job)


def add_event(db: Session, job_id: uuid.UUID, message: str, *, level: str = "info", stage: str | None = None,
              data: dict | None = None) -> None:
    db.add(JobEvent(job_id=job_id, message=message, level=level, stage=stage, data=data or {}))


def save_checkpoint(job_id: uuid.UUID, checkpoint: dict[str, Any]) -> None:
    with session_scope() as db:
        job = db.get(Job, job_id)
        if job is not None:
            job.checkpoint = {**(job.checkpoint or {}), **checkpoint}


# ------------------------------------------------------------ transitions
def complete(job_id: uuid.UUID, result: dict[str, Any] | None = None, *, label: str = "Completed") -> None:
    with session_scope() as db:
        job = db.get(Job, job_id)
        if job is None or job.status in TERMINAL:
            return
        job.status = "completed"
        job.status_label = label
        job.result = result or {}
        job.progress = 1.0
        job.finished_at = utcnow()
        job.locked_by = None
        job.lease_expires_at = None
        job.resume_value = None
        db.add(JobEvent(job_id=job_id, message=f"Completed: {label}", stage="completed"))
        _progress_event(job)
        _after_terminal(db, job)


def fail(job_id: uuid.UUID, error: dict[str, Any], *, retryable: bool, label: str = "Failed") -> str:
    """Record a failure; re-queue with backoff when allowed. Returns new status."""
    with session_scope() as db:
        job = db.get(Job, job_id)
        if job is None:
            return "missing"
        job.error = error
        job.locked_by = None
        job.lease_expires_at = None
        if retryable and job.attempts < job.max_attempts and not job.cancel_requested:
            delay = min(600, 10 * (3 ** (job.attempts - 1)))
            job.status = "queued"
            job.status_label = "Retrying"
            job.run_after = utcnow() + timedelta(seconds=delay)
            db.add(JobEvent(job_id=job_id, level="warning", stage="retry",
                            message=f"Attempt {job.attempts} failed ({error.get('message', '')[:200]}); retrying in {delay}s"))
            _signal(job.queue)
            return "queued"
        job.status = "failed"
        job.status_label = label
        job.finished_at = utcnow()
        db.add(JobEvent(job_id=job_id, level="error", stage="failed", message=error.get("message", "Failed")[:2000],
                        data={k: v for k, v in error.items() if k != "message"}))
        _progress_event(job)
        _after_terminal(db, job)
        return "failed"


def mark_waiting(job_id: uuid.UUID, *, label: str, info: dict[str, Any] | None = None) -> None:
    """Workflow suspended (waiting for children or for human approval)."""
    with session_scope() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        job.status = "waiting"
        job.status_label = label
        job.locked_by = None
        job.lease_expires_at = None
        job.resume_value = None
        # A suspension is a successful stop, not a failed attempt: resuming must
        # not eat into the retry budget of later stages.
        job.attempts = max(0, job.attempts - 1)
        job.checkpoint = {**(job.checkpoint or {}), "waiting": info or {}}
        db.add(JobEvent(job_id=job_id, message=f"Waiting: {label}", stage="waiting", data=info or {}))
        _progress_event(job)
        # Children may already have finished before the parent suspended.
        group = (info or {}).get("wait_group")
        if group:
            _maybe_resume_parent(db, job, group)


def mark_paused(job_id: uuid.UUID, label: str = "Paused") -> None:
    with session_scope() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        job.status = "paused"
        job.status_label = label
        job.locked_by = None
        job.lease_expires_at = None
        job.attempts = max(0, job.attempts - 1)  # pausing is not a failed attempt
        checkpoint = dict(job.checkpoint or {})
        checkpoint.pop("pause_requested", None)
        job.checkpoint = checkpoint
        db.add(JobEvent(job_id=job_id, message="Paused", stage="paused"))
        _progress_event(job)


def mark_cancelled(job_id: uuid.UUID, reason: str = "Cancelled") -> None:
    with session_scope() as db:
        job = db.get(Job, job_id)
        if job is None or job.status in TERMINAL:
            return
        _cancel_row(db, job, reason)


def _cancel_row(db: Session, job: Job, reason: str) -> None:
    job.status = "cancelled"
    job.status_label = "Cancelled"
    job.finished_at = utcnow()
    job.locked_by = None
    job.lease_expires_at = None
    job.error = {"message": reason}
    db.add(JobEvent(job_id=job.id, level="warning", message=reason, stage="cancelled"))
    _progress_event(job)
    for child in db.execute(select(Job).where(Job.parent_id == job.id, Job.status.in_(ACTIVE))).scalars():
        if child.status == "running":
            child.cancel_requested = True
        else:
            _cancel_row(db, child, f"Parent cancelled: {reason}")


def _after_terminal(db: Session, job: Job) -> None:
    if job.parent_id is None or job.wait_group is None:
        return
    parent = db.execute(select(Job).where(Job.id == job.parent_id).with_for_update()).scalar_one_or_none()
    if parent is not None:
        _maybe_resume_parent(db, parent, job.wait_group)


def _maybe_resume_parent(db: Session, parent: Job, group: str) -> None:
    if parent.status != "waiting":
        return
    waiting_for = (parent.checkpoint or {}).get("waiting", {}).get("wait_group")
    if waiting_for != group:
        return
    children = db.execute(select(Job).where(Job.parent_id == parent.id, Job.wait_group == group)).scalars().all()
    if not children or any(child.status not in TERMINAL for child in children):
        return
    parent.resume_value = {
        "children": {
            child.payload.get("child_key", str(child.id)): {
                "job_id": str(child.id), "status": child.status, "result": child.result, "error": child.error,
            }
            for child in children
        }
    }
    parent.status = "queued"
    parent.status_label = parent.status_label or "Resuming"
    parent.run_after = utcnow()
    db.add(JobEvent(job_id=parent.id, message=f"All '{group}' steps finished; resuming", stage="resume"))
    _signal(parent.queue)


def resume_with(db: Session, job_id: uuid.UUID, value: Any, *, label: str = "Resuming") -> Job:
    """Resume a waiting workflow with an explicit value (e.g., human approval)."""
    job = db.execute(select(Job).where(Job.id == job_id).with_for_update()).scalar_one()
    if job.status not in ("waiting", "paused"):
        raise ValueError(f"Job is {job.status}, not waiting")
    job.resume_value = value
    job.status = "queued"
    job.status_label = label
    job.run_after = utcnow()
    db.add(JobEvent(job_id=job.id, message=label, stage="resume"))
    _signal(job.queue)
    return job


# ---------------------------------------------------------- user actions
def cancel(db: Session, job_id: uuid.UUID, reason: str = "Cancelled by user") -> Job:
    job = db.execute(select(Job).where(Job.id == job_id).with_for_update()).scalar_one()
    if job.status in TERMINAL:
        return job
    if job.status == "running":
        job.cancel_requested = True
        db.add(JobEvent(job_id=job.id, level="warning", message="Cancellation requested", stage="cancel"))
        for child in db.execute(select(Job).where(Job.parent_id == job.id, Job.status.in_(ACTIVE))).scalars():
            if child.status == "running":
                child.cancel_requested = True
            else:
                _cancel_row(db, child, f"Parent cancelled: {reason}")
    else:
        _cancel_row(db, job, reason)
    return job


def pause(db: Session, job_id: uuid.UUID) -> Job:
    job = db.execute(select(Job).where(Job.id == job_id).with_for_update()).scalar_one()
    if job.status == "queued":
        job.status = "paused"
        job.status_label = "Paused"
    elif job.status in ("running", "waiting"):
        # Takes effect at the next safe checkpoint.
        job.checkpoint = {**(job.checkpoint or {}), "pause_requested": True}
        if job.status == "waiting":
            job.checkpoint = {**job.checkpoint, "paused_while_waiting": True}
    db.add(JobEvent(job_id=job.id, message="Pause requested", stage="pause"))
    return job


def unpause(db: Session, job_id: uuid.UUID) -> Job:
    job = db.execute(select(Job).where(Job.id == job_id).with_for_update()).scalar_one()
    checkpoint = dict(job.checkpoint or {})
    checkpoint.pop("pause_requested", None)
    checkpoint.pop("paused_while_waiting", None)
    job.checkpoint = checkpoint
    if job.status == "paused":
        job.status = "queued"
        job.status_label = "Resuming"
        job.run_after = utcnow()
        _signal(job.queue)
    db.add(JobEvent(job_id=job.id, message="Resumed by user", stage="resume"))
    return job


def retry(db: Session, job_id: uuid.UUID) -> Job:
    job = db.execute(select(Job).where(Job.id == job_id).with_for_update()).scalar_one()
    if job.status not in ("failed", "cancelled"):
        raise ValueError("Only failed or cancelled jobs can be retried")
    job.status = "queued"
    job.status_label = "Retrying"
    job.cancel_requested = False
    job.max_attempts = job.attempts + 2  # this manual retry plus one automatic retry
    job.run_after = utcnow()
    job.finished_at = None
    job.error = None
    job.resume_value = None  # the outcome it failed on is stale now; fresh results resume it
    db.add(JobEvent(job_id=job.id, message="Retry requested; resuming from last checkpoint", stage="retry"))
    if job.parent_id is None and job.project_id is not None:
        project = db.get(Project, job.project_id)
        if project is not None and project.status in ("failed", "cancelled"):
            project.status, project.status_label = "running", "Retrying"
    # Failed/cancelled children are re-run too; completed children are kept,
    # so a retried workflow never repeats work that already succeeded.
    for child in db.execute(select(Job).where(Job.parent_id == job.id, Job.status.in_(("failed", "cancelled")))).scalars():
        child.status = "queued"
        child.status_label = "Retrying"
        child.cancel_requested = False
        child.max_attempts = child.attempts + 2
        child.run_after = utcnow()
        child.finished_at = None
        child.error = None
        db.add(JobEvent(job_id=child.id, message="Re-queued by parent retry", stage="retry"))
        _signal(child.queue)
    # A failed child re-run should be able to resume its waiting parent again.
    if job.parent_id:
        parent = db.get(Job, job.parent_id)
        if parent is not None and parent.status == "failed":
            parent.status = "waiting"
            parent.status_label = "Waiting for retried step"
            parent.finished_at = None
    _signal(job.queue)
    return job


def cancel_all_active(db: Session, *, reason: str) -> int:
    count = 0
    for job in db.execute(select(Job).where(Job.status.in_(ACTIVE), Job.parent_id.is_(None))).scalars():
        cancel(db, job.id, reason)
        count += 1
    db.execute(update(Job).where(Job.status.in_(("queued", "waiting", "paused"))).values(
        status="cancelled", status_label="Cancelled", finished_at=utcnow(), error={"message": reason}))
    return count


def recover_stale(lease_grace_seconds: int = 0) -> int:
    """Re-queue running jobs whose worker stopped heart-beating."""
    recovered = 0
    with session_scope() as db:
        cutoff = utcnow() - timedelta(seconds=lease_grace_seconds)
        stale = db.execute(
            select(Job).where(Job.status == "running", Job.lease_expires_at < cutoff).with_for_update(skip_locked=True)
        ).scalars().all()
        for job in stale:
            if job.cancel_requested:
                _cancel_row(db, job, "Cancelled (worker stopped)")
                continue
            job.status = "queued"
            job.status_label = "Resuming after interruption"
            job.locked_by = None
            job.lease_expires_at = None
            job.run_after = utcnow()
            db.add(JobEvent(job_id=job.id, level="warning", stage="recovery",
                            message="Worker stopped responding; job re-queued and will resume from its last checkpoint"))
            _signal(job.queue)
            recovered += 1
    if recovered:
        log.warning("Recovered %d stale job(s)", recovered)
    return recovered


def job_summary(job: Job) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "type": job.type,
        "queue": job.queue,
        "status": job.status,
        "label": job.status_label,
        "stage": job.stage,
        "progress": round(job.progress, 3),
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "error": job.error,
        "project_id": str(job.project_id) if job.project_id else None,
        "parent_id": str(job.parent_id) if job.parent_id else None,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "waiting": (job.checkpoint or {}).get("waiting"),
        "detail": (job.checkpoint or {}).get("detail") if job.status == "running" else None,
    }
