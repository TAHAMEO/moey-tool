"""Analytics collection and learning jobs."""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select

from ..analytics import collect as collector
from ..analytics import retention
from ..db.base import session_scope, utcnow
from ..db.models import MetricSnapshot, Publication
from ..jobs import queue
from ..jobs.registry import JobContext, PermanentError, RetryableError, handler
from ..learning import engine
from ..platforms.http import PlatformError
from ..services import app_settings


@handler("analytics.collect", queue="default", needs_secrets=True)
def collect_job(ctx: JobContext) -> dict:
    with session_scope() as db:
        publication = db.get(Publication, uuid.UUID(ctx.payload["publication_id"]))
        if publication is None or publication.status != "published":
            return {"skipped": "not published"}
        try:
            snapshot = collector.collect(db, publication)
        except PlatformError as exc:
            if exc.retryable:
                raise RetryableError(str(exc)) from exc
            raise PermanentError(f"Analytics unavailable: {exc}", code="analytics") from exc
        result = {"metrics": snapshot.metrics, "unavailable": list(snapshot.unavailable), "age_hours": snapshot.age_hours}
        shorts = app_settings.get_section(db, "shorts")
        if shorts.retention_loop:  # early-retention signals and flop flags on every clip
            report = retention.evaluate(db, shorts)
            result["flops"] = len(report["flops"])
    # Enough fresh data can change what we know: schedule a learning pass.
    with session_scope() as db:
        queue.enqueue(db, "learning.run", {}, dedupe_key="learning-run", run_after=utcnow() + timedelta(minutes=10),
                      priority=200, max_attempts=2)
    return result


@handler("analytics.collect_due", queue="default", needs_secrets=True)
def collect_due(ctx: JobContext) -> dict:
    """Safety net: published videos without a snapshot in 24 h get one (≤ 30 days old)."""
    created = 0
    with session_scope() as db:
        for publication in db.execute(select(Publication).where(Publication.status == "published",
                                                                Publication.published_at >= utcnow() - timedelta(days=30))).scalars():
            latest = db.execute(select(MetricSnapshot.collected_at).where(MetricSnapshot.publication_id == publication.id)
                                .order_by(MetricSnapshot.collected_at.desc())).scalars().first()
            if latest is None or latest < utcnow() - timedelta(hours=24):
                queue.enqueue(db, "analytics.collect", {"publication_id": str(publication.id)},
                              dedupe_key=f"analytics-due:{publication.id}", priority=190, max_attempts=3)
                created += 1
    return {"enqueued": created}


@handler("learning.run", queue="default")
def learning_job(ctx: JobContext) -> dict:
    with session_scope() as db:
        return engine.run(db)
