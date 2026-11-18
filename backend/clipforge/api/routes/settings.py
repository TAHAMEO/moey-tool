"""Settings, global controls (pause / emergency stop) and spend reporting."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...db.base import get_db
from ...db.models import AIUsage, AuditLog
from ...providers.hub import ModelHub
from ...services import app_settings, control
from ..deps import Principal, admin, client_ip, viewer

router = APIRouter(tags=["settings"])


@router.get("/settings")
def get_settings_all(db: Session = Depends(get_db), _: Principal = Depends(viewer)) -> dict:
    return app_settings.get_all(db)


@router.patch("/settings/{section}")
def patch_settings(section: str, patch: dict[str, Any], request: Request, db: Session = Depends(get_db),
                   principal: Principal = Depends(admin)) -> dict:
    if section == "control":
        raise HTTPException(400, {"code": "use_control", "message": "Use the control endpoints."})
    try:
        updated = app_settings.update_section(db, section, patch, user_id=principal.id, ip=client_ip(request))
    except app_settings.SettingsError as exc:
        raise HTTPException(400, {"code": "invalid", "message": str(exc)}) from exc
    return updated.model_dump()


@router.post("/control/{action}")
def control_action(action: str, request: Request, db: Session = Depends(get_db),
                   principal: Principal = Depends(admin)) -> dict:
    try:
        return control.apply(db, action, user_id=principal.id, ip=client_ip(request))
    except ValueError as exc:
        raise HTTPException(400, {"code": "invalid", "message": str(exc)}) from exc


@router.get("/usage/summary")
def usage_summary(db: Session = Depends(get_db), _: Principal = Depends(viewer)) -> dict:
    now = datetime.now(UTC)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    ai = app_settings.get_section(db, "ai")
    by_provider = db.execute(
        select(AIUsage.provider_type, AIUsage.model, func.count(), func.sum(AIUsage.cost_usd),
               func.sum(AIUsage.input_tokens), func.sum(AIUsage.output_tokens))
        .where(AIUsage.created_at >= month)
        .group_by(AIUsage.provider_type, AIUsage.model)
    ).all()
    by_task = db.execute(
        select(AIUsage.task, func.count(), func.sum(AIUsage.cost_usd)).where(AIUsage.created_at >= month)
        .group_by(AIUsage.task)
    ).all()
    cached = db.execute(select(func.count()).where(AIUsage.cached.is_(True), AIUsage.created_at >= month)).scalar_one()
    failures = db.execute(select(func.count()).where(AIUsage.success.is_(False), AIUsage.created_at >= month)).scalar_one()
    estimated = db.execute(select(func.count()).where(AIUsage.cost_estimated.is_(True), AIUsage.created_at >= month)).scalar_one()
    return {
        "today_usd": round(ModelHub.spend(db, since=day), 4),
        "month_usd": round(ModelHub.spend(db, since=month), 4),
        "budgets": {"daily": ai.daily_budget_usd, "monthly": ai.monthly_budget_usd, "per_job": ai.per_job_budget_usd},
        "cache_hits_month": cached,
        "failed_calls_month": failures,
        "calls_with_estimated_prices": estimated,
        "by_model": [
            {"provider_type": r[0], "model": r[1], "calls": r[2], "cost_usd": round(float(r[3] or 0), 4),
             "input_tokens": int(r[4] or 0), "output_tokens": int(r[5] or 0)}
            for r in by_provider
        ],
        "by_task": [{"task": r[0], "calls": r[1], "cost_usd": round(float(r[2] or 0), 4)} for r in by_task],
    }


@router.get("/audit")
def audit_log(limit: int = 100, db: Session = Depends(get_db), _: Principal = Depends(admin)) -> list[dict]:
    rows = db.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(min(limit, 500))).scalars()
    return [{"id": str(r.id), "action": r.action, "target_type": r.target_type, "target_id": r.target_id,
             "details": r.details, "ip": r.ip, "user_id": str(r.user_id) if r.user_id else None,
             "created_at": r.created_at} for r in rows]

