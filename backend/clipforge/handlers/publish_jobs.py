"""Publishing jobs (default queue: they need OAuth tokens)."""

from __future__ import annotations

import uuid

from ..db.base import session_scope
from ..db.models import OutputPackage
from ..jobs import queue
from ..jobs.registry import JobContext, PermanentError, RetryableError, handler
from ..publishing import service


@handler("workflow.publish", queue="default", needs_secrets=True)
def publish_job(ctx: JobContext) -> dict:
    package_id = uuid.UUID(ctx.payload["package_id"])
    ctx.progress(0.05, label="Publishing", stage="publish")
    try:
        result = service.publish(package_id, on_progress=lambda f: ctx.progress(0.1 + 0.8 * f, label="Uploading"),
                                 cancelled=ctx.cancelled)
    except service.PublishError as exc:
        if exc.reschedule_at is not None:
            with session_scope() as db:
                package = db.get(OutputPackage, package_id)
                package.status = "scheduled"
                queue.enqueue(db, "workflow.publish", {"package_id": str(package_id)}, run_after=exc.reschedule_at,
                              dedupe_key=f"publish-later:{package_id}", status_label="Scheduled",
                              project_id=ctx.project_id, user_id=ctx.user_id)
            return {"_label": "Scheduled", "scheduled_at": exc.reschedule_at.isoformat(), "reason": str(exc)}
        if exc.retryable:
            raise RetryableError(str(exc)) from exc
        with session_scope() as db:
            package = db.get(OutputPackage, package_id)
            if package is not None and exc.code not in ("draft_only",):
                package.status = "failed"
        raise PermanentError(str(exc), code=exc.code, label="Not published") from exc
    label = "Already published" if result.get("duplicate") else "Published"
    return {"_label": label, **result}
