"""Job inspection and control: cancel, retry, pause, resume, approve."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.base import get_db
from ...db.models import Job, JobEvent
from ...jobs import queue
from ...security.audit import audit
from ..deps import Principal, client_ip, editor, viewer

router = APIRouter(prefix="/jobs", tags=["jobs"])


class Approval(BaseModel):
    approved: list[uuid.UUID] = Field(default_factory=list)
    rejected: list[uuid.UUID] = Field(default_factory=list)


def _get(db: Session, job_id: uuid.UUID) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, {"code": "not_found", "message": "Job not found."})
    return job


@router.get("")
def list_jobs(active: bool = False, project_id: uuid.UUID | None = None, limit: int = 50, db: Session = Depends(get_db),
              _: Principal = Depends(viewer)) -> list[dict]:
    query = select(Job).order_by(Job.created_at.desc()).limit(min(limit, 200))
    if active:
        query = query.where(Job.status.in_(queue.ACTIVE))
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
    queue.resume_with(db, job_id, {"approved": approved, "rejected": [str(p) for p in body.rejected], "by": str(principal.id)},
                      label="Publishing approved clips" if approved else "Finishing without publishing")
    audit(db, "publish.approve", user_id=principal.id, target_type="job", target_id=job_id,
          details={"approved": approved}, ip=client_ip(request))
    return {"ok": True, "approved": approved}


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
