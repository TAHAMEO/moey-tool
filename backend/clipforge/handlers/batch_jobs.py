"""Batch jobs: watching folders and taking in their videos."""

from __future__ import annotations

import uuid

from ..batches import service
from ..db.base import session_scope
from ..jobs.registry import JobContext, handler


@handler("folders.scan", queue="default")
def scan_folders(ctx: JobContext) -> dict:
    """Every minute: queue new, completely written videos from the watched folders (held while paused)."""
    with session_scope() as db:
        found = service.scan_all(db)
    return {"queued": found, "_label": f"{sum(found.values())} new video(s)" if any(found.values()) else "No new videos"}


@handler("batch.ingest_file", queue="media")
def ingest_file(ctx: JobContext) -> dict:
    """Copy a watched-folder video into storage and start its project (media worker: no secrets)."""
    ctx.progress(0.05, label="Copying video", stage="ingest")
    with session_scope() as db:
        result = service.ingest_file(db, uuid.UUID(ctx.payload["item_id"]), cancelled=ctx.cancelled)
    return {**result, "_label": "Started" if result.get("project_id") else "Skipped"}
