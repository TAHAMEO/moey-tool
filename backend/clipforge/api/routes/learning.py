"""Analytics and the Learning Center: findings, strategies, experiments."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...analytics import compare
from ...db.base import get_db, utcnow
from ...db.models import Experiment, LearningRecord, MetricSnapshot, OutputPackage, Publication, Strategy
from ...jobs import queue
from ...learning import engine
from ...security.audit import audit
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
