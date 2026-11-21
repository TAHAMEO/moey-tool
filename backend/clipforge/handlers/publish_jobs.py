"""Publishing jobs (default queue: they need OAuth tokens)."""

from __future__ import annotations

import uuid

from ..db.base import session_scope
from ..db.models import Clip, OutputPackage
from ..jobs.registry import JobContext, PermanentError, RetryableError, handler
from ..publishing import schedule, service


@handler("workflow.publish", queue="default", needs_secrets=True)
def publish_job(ctx: JobContext) -> dict:
    package_id = uuid.UUID(ctx.payload["package_id"])
    ctx.progress(0.05, label="Publishing", stage="publish")
    try:
        result = service.publish(package_id, on_progress=lambda f: ctx.progress(0.1 + 0.8 * f, label="Uploading"),
                                 cancelled=ctx.cancelled)
    except service.PublishError as exc:
        if exc.code == "on_hold":
            # Publishing is on hold: the post keeps its time; the schedule check re-queues it when publishing resumes.
            return {"_label": "On hold", "on_hold": True, "message": str(exc)}
        if exc.code == "unscheduled":
            return {"_label": "Schedule cancelled", "message": str(exc)}
        if exc.reschedule_at is not None:
            with session_scope() as db:
                package = db.get(OutputPackage, package_id)
                if package.status != "scheduled":
                    package.status = "scheduled"  # held back by a daily limit or quota (no owner-chosen time)
                schedule.ensure_job(db, package, user_id=ctx.user_id, exclude=ctx.job_id, not_before=exc.reschedule_at)
                schedule.refresh_project_status(db, db.get(Clip, package.clip_id).project_id)
            return {"_label": "Scheduled", "scheduled_at": exc.reschedule_at.isoformat(), "reason": str(exc)}
        if exc.retryable:
            raise RetryableError(str(exc)) from exc
        with session_scope() as db:
            package = db.get(OutputPackage, package_id)
            if package is not None and exc.code not in ("draft_only",):
                package.status = "missed" if exc.code == "missed" else "failed"
                schedule.refresh_project_status(db, db.get(Clip, package.clip_id).project_id)
        label = "Missed its time" if exc.code == "missed" else "Not published"
        raise PermanentError(str(exc), code=exc.code, label=label) from exc
    if result.get("scheduled_at"):
        return {"_label": "Scheduled", **result}
    label = "Already published" if result.get("duplicate") else "Published"
    return {"_label": label, **result}
