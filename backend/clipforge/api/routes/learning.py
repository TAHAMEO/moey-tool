"""Analytics and the Learning Center: findings, strategies, experiments."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...analytics import compare, costs, retention
from ...db.base import get_db, utcnow
from ...db.models import Experiment, LearningRecord, MetricSnapshot, OutputPackage, Publication, Strategy
from ...jobs import queue
from ...learning import engine
from ...security.audit import audit
from ...services import app_settings
from ..deps import Principal, admin, client_ip, editor, viewer

router = APIRouter(tags=["learning"])


class RecordReview(BaseModel):
    status: Literal["active", "rejected"]


@router.get("/analytics/summary")
def analytics_summary(platform: str | None = None, db: Session = Depends(get_db), _: Principal = Depends(viewer)) -> dict:
    return compare.summary(db, platform)


@router.get("/analytics/publications")
def publications(db: Session = Depends(get_db), _: Principal = Depends(viewer)) -> list[dict]:
    out = []
    rows = db.execute(select(Publication, OutputPackage).join(OutputPackage, OutputPackage.id == Publication.package_id)
                      .order_by(Publication.created_at.desc()).limit(200)).all()
    for pub, pkg in rows:
        snaps = db.execute(select(MetricSnapshot).where(MetricSnapshot.publication_id == pub.id)
                           .order_by(MetricSnapshot.collected_at)).scalars().all()
        out.append({"id": str(pub.id), "platform": pub.platform, "status": pub.status, "title": pkg.title or pkg.caption[:80],
                    "url": pub.external_url, "published_at": pub.published_at, "last_error": pub.last_error,
                    "snapshots": [{"at": s.collected_at, "age_hours": s.age_hours, "metrics": s.metrics,
                                   "unavailable": s.unavailable} for s in snaps]})
    return out


@router.post("/analytics/collect/{publication_id}")
def collect_now(publication_id: uuid.UUID, db: Session = Depends(get_db), _: Principal = Depends(editor)) -> dict:
    job = queue.enqueue(db, "analytics.collect", {"publication_id": str(publication_id)},
                        dedupe_key=f"analytics-manual:{publication_id}")
    return {"job_id": str(job.id)}


MAX_IMPORT_BYTES = 5 * 1024 * 1024


@router.post("/analytics/import")
async def import_metrics(request: Request, file: UploadFile = File(...), platform: str | None = Form(None),
                         db: Session = Depends(get_db), principal: Principal = Depends(editor)) -> dict:
    """Metrics the APIs don't give (YouTube Studio's "viewed vs. swiped away", Instagram skip rate,
    …) from a CSV export. Rows are matched to published clips by video id or exact title."""
    if platform not in (None, "", "youtube", "instagram"):
        raise HTTPException(400, {"code": "platform", "message": "Platform must be youtube or instagram."})
    data = await file.read(MAX_IMPORT_BYTES + 1)
    if len(data) > MAX_IMPORT_BYTES:
        raise HTTPException(413, {"code": "too_large", "message": "CSV files up to 5 MB."})
    try:
        rows = retention.parse_csv(data)
    except Exception as exc:  # any parse problem is the user's file
        raise HTTPException(400, {"code": "csv", "message": f"Could not read the CSV: {exc}"[:300]}) from exc
    result = retention.import_rows(db, rows, platform=platform or None,
                                   source=f"csv_import:{(file.filename or 'upload.csv')[:40]}")
    if result.imported:
        retention.evaluate(db, app_settings.get_section(db, "shorts"))
    audit(db, "analytics.import", user_id=principal.id, target_type="analytics", target_id=None,
          details={"imported": result.imported, "unmatched": len(result.unmatched)}, ip=client_ip(request))
    return result.to_dict()


class ManualMetrics(BaseModel):
    values: dict[str, float]


@router.post("/analytics/publications/{publication_id}/metrics")
def manual_metrics(publication_id: uuid.UUID, body: ManualMetrics, request: Request, db: Session = Depends(get_db),
                   principal: Principal = Depends(editor)) -> dict:
    """Numbers typed in by you (e.g. read off YouTube Studio)."""
    try:
        snap = retention.manual_entry(db, publication_id, body.values)
    except ValueError as exc:
        raise HTTPException(400, {"code": "metrics", "message": str(exc)}) from exc
    retention.evaluate(db, app_settings.get_section(db, "shorts"))
    audit(db, "analytics.manual", user_id=principal.id, target_type="publication", target_id=publication_id,
          ip=client_ip(request))
    return {"ok": True, "metrics": snap.metrics}


@router.get("/analytics/retention")
def retention_report(db: Session = Depends(get_db), _: Principal = Depends(viewer)) -> dict:
    """Early retention per clip, flops to re-edit or delete, hook/title styles ranked by what kept
    viewers, and the source segments that made the strongest openings."""
    return retention.evaluate(db, app_settings.get_section(db, "shorts"))


@router.get("/analytics/costs")
def cost_report(db: Session = Depends(get_db), _: Principal = Depends(viewer)) -> dict:
    """What making and posting Shorts cost: metered AI spend and processing time, per published Short."""
    return costs.channel_costs(db)


@router.get("/analytics/ab-tests")
def ab_tests(db: Session = Depends(get_db), _: Principal = Depends(viewer)) -> list[dict]:
    from ...shorts import abtest

    return abtest.evaluate(db)


@router.get("/learning/records")
def records(db: Session = Depends(get_db), _: Principal = Depends(viewer)) -> list[dict]:
    rows = db.execute(select(LearningRecord).order_by(LearningRecord.created_at.desc()).limit(200)).scalars()
    return [{"id": str(r.id), "kind": r.kind, "finding": r.finding, "evidence": r.evidence, "platform": r.platform,
             "confidence": r.confidence, "limitations": r.limitations, "causal_claim": r.causal_claim,
             "strategy_id": str(r.strategy_id) if r.strategy_id else None, "next_experiment": r.next_experiment,
             "related_publications": len(r.related_publication_ids), "status": r.status, "created_at": r.created_at}
            for r in rows]


@router.patch("/learning/records/{record_id}")
def review_record(record_id: uuid.UUID, body: RecordReview, request: Request, db: Session = Depends(get_db),
                  principal: Principal = Depends(editor)) -> dict:
    record = db.get(LearningRecord, record_id)
    if record is None:
        raise HTTPException(404, {"code": "not_found", "message": "Record not found."})
    record.status = body.status
    audit(db, "learning.review", user_id=principal.id, target_type="learning_record", target_id=record_id,
          details={"status": body.status}, ip=client_ip(request))
    return {"ok": True}


@router.get("/learning/strategies")
def strategies(db: Session = Depends(get_db), _: Principal = Depends(viewer)) -> list[dict]:
    engine.ensure_default_strategy(db)
    rows = db.execute(select(Strategy).order_by(Strategy.version.desc())).scalars()
    return [{"id": str(s.id), "version": s.version, "name": s.name, "status": s.status, "params": s.params,
             "rationale": s.rationale, "parent_id": str(s.parent_id) if s.parent_id else None,
             "created_at": s.created_at, "activated_at": s.activated_at} for s in rows]


@router.post("/learning/strategies/{strategy_id}/activate")
def activate(strategy_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
             principal: Principal = Depends(admin)) -> dict:
    strategy = db.get(Strategy, strategy_id)
    if strategy is None:
        raise HTTPException(404, {"code": "not_found", "message": "Strategy not found."})
    for other in db.execute(select(Strategy).where(Strategy.status == "active")).scalars():
        other.status = "retired"
    strategy.status = "active"
    strategy.activated_at = utcnow()
    audit(db, "strategy.activate", user_id=principal.id, target_type="strategy", target_id=strategy_id,
          details={"version": strategy.version}, ip=client_ip(request))
    return {"ok": True}


@router.get("/learning/experiments")
def experiments(db: Session = Depends(get_db), _: Principal = Depends(viewer)) -> list[dict]:
    rows = db.execute(select(Experiment).order_by(Experiment.started_at.desc())).scalars()
    return [{"id": str(e.id), "name": e.name, "hypothesis": e.hypothesis, "platform": e.platform, "status": e.status,
             "allocation": e.allocation, "metric": e.metric, "min_samples_per_arm": e.min_samples_per_arm,
             "result": e.result, "started_at": e.started_at, "ended_at": e.ended_at,
             "control_strategy_id": str(e.control_strategy_id), "variant_strategy_id": str(e.variant_strategy_id)}
            for e in rows]


@router.post("/learning/run")
def run_learning(db: Session = Depends(get_db), _: Principal = Depends(editor)) -> dict:
    job = queue.enqueue(db, "learning.run", {}, dedupe_key="learning-run")
    return {"job_id": str(job.id)}
