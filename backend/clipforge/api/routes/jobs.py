"""Job inspection and control: cancel, retry, pause, resume, approve."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.base import get_db
from ...db.models import Job, JobEvent, OutputPackage
from ...jobs import queue
from ...publishing import schedule
from ...security.audit import audit
from ..deps import Principal, client_ip, editor, viewer

router = APIRouter(prefix="/jobs", tags=["jobs"])


class Approval(BaseModel):
    approved: list[uuid.UUID] = Field(default_factory=list)
    rejected: list[uuid.UUID] = Field(default_factory=list)
    #: Publish at this time instead of now (ISO 8601 with a time zone).
    scheduled_at: datetime | None = None
    #: Several clips: one every N minutes from ``scheduled_at`` (same clip on both platforms: same time).
    every_minutes: int | None = Field(None, ge=1, le=43200)
    #: Publish immediately even though a time was asked for in chat.
    publish_now: bool = False


def _get(db: Session, job_id: uuid.UUID) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, {"code": "not_found", "message": "Job not found."})
    return job


#: Background housekeeping, not work the owner started: never listed as "working on now".
ROUTINE_PREFIXES = ("maintenance.", "analytics.", "learning.", "folders.", "memory.")


@router.get("")
def list_jobs(active: bool = False, project_id: uuid.UUID | None = None, limit: int = 50, db: Session = Depends(get_db),
              _: Principal = Depends(viewer)) -> list[dict]:
    from sqlalchemy import and_, not_, or_

    from ...db.base import utcnow

    query = select(Job).order_by(Job.created_at.desc()).limit(min(limit, 200))
    if active:
        # Work in progress only: no housekeeping, and no post waiting in the queue for a later time
        # (those are listed under Dashboard → Upcoming posts).
        query = query.where(Job.status.in_(queue.ACTIVE),
                            not_(or_(*[Job.type.startswith(p) for p in ROUTINE_PREFIXES])),
                            not_(and_(Job.status == "queued", Job.run_after > utcnow() + timedelta(seconds=60))))
    if project_id:
        query = query.where(Job.project_id == project_id)
    else:
        query = query.where(Job.parent_id.is_(None))
    return [queue.job_summary(j) for j in db.execute(query).scalars()]


@router.get("/{job_id}")
def get_job(job_id: uuid.UUID, db: Session = Depends(get_db), _: Principal = Depends(viewer)) -> dict:
    job = _get(db, job_id)
    events = db.execute(select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.created_at).limit(300)).scalars()
    children = db.execute(select(Job).where(Job.parent_id == job_id).order_by(Job.created_at)).scalars()
    return {
        **queue.job_summary(job),
        "result": job.result,
        "events": [{"at": e.created_at, "level": e.level, "stage": e.stage, "message": e.message} for e in events],
        "children": [queue.job_summary(c) for c in children],
    }


@router.post("/{job_id}/approve")
def approve(job_id: uuid.UUID, body: Approval, request: Request, db: Session = Depends(get_db),
            principal: Principal = Depends(editor)) -> dict:
    job = _get(db, job_id)
    waiting = (job.checkpoint or {}).get("waiting", {})
    if job.status != "waiting" or waiting.get("type") != "approval":
        raise HTTPException(409, {"code": "state", "message": "This job is not waiting for approval."})
    allowed = {p["package_id"] for p in waiting.get("packages", [])}
    approved = [str(p) for p in body.approved if str(p) in allowed]
    times: dict[str, str] = {}
    if body.scheduled_at is not None and approved:
        entries = [(db.get(OutputPackage, uuid.UUID(p["package_id"])), int(p.get("rank") or 0))
                   for p in waiting.get("packages", []) if p["package_id"] in approved]
        try:
            times = {k: v.isoformat() for k, v in schedule.apply_plan(
                db, [(pkg, rank) for pkg, rank in entries if pkg is not None], body.scheduled_at, body.every_minutes).items()}
        except schedule.ScheduleError as exc:
            raise HTTPException(409 if exc.code == "daily_limit" else 400, {"code": exc.code, "message": str(exc)}) from exc
    label = ("Scheduling approved clips" if times else "Publishing approved clips") if approved else "Finishing without publishing"
    queue.resume_with(db, job_id, {"approved": approved, "rejected": [str(p) for p in body.rejected], "by": str(principal.id),
                                   "scheduled": times, "publish_now": body.publish_now and not times}, label=label)
    audit(db, "publish.approve", user_id=principal.id, target_type="job", target_id=job_id,
          details={"approved": approved, "scheduled": times}, ip=client_ip(request))
    return {"ok": True, "approved": approved, "scheduled": times}


@router.post("/{job_id}/{action}")
def job_action(job_id: uuid.UUID, action: str, request: Request, db: Session = Depends(get_db),
               principal: Principal = Depends(editor)) -> dict:
    _get(db, job_id)
    try:
        if action == "cancel":
            job = queue.cancel(db, job_id)
        elif action == "retry":
            job = queue.retry(db, job_id)
        elif action == "pause":
            job = queue.pause(db, job_id)
        elif action == "resume":
            job = queue.unpause(db, job_id)
        else:
            raise HTTPException(400, {"code": "invalid", "message": "Unknown action."})
    except ValueError as exc:
        raise HTTPException(409, {"code": "state", "message": str(exc)}) from exc
    audit(db, f"job.{action}", user_id=principal.id, target_type="job", target_id=job_id, ip=client_ip(request))
    db.flush()
    return queue.job_summary(job)
