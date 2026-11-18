"""Liveness, readiness and admin diagnostics."""

from __future__ import annotations

import shutil
from datetime import timedelta

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ...config import get_settings
from ...db.base import get_db, utcnow
from ...db.models import WorkerHeartbeat
from ...media import ffmpeg_tools
from ...media import models as model_registry
from ...services.redis_client import get_redis
from ..deps import Principal, admin

router = APIRouter(tags=["health"])


def _checks(db: Session) -> dict:
    checks: dict = {}
    try:
        db.execute(text("SELECT 1"))
        version = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
        checks["database"] = {"ok": True, "schema_version": version}
    except Exception as exc:  # noqa: BLE001 - pragma: no cover - reported, not raised
        checks["database"] = {"ok": False, "error": exc.__class__.__name__}
    checks["redis"] = {"ok": get_redis() is not None, "required": False}
    ff = ffmpeg_tools.capabilities()
    checks["ffmpeg"] = {"ok": ff.get("available", False) and ff.get("libass", False), **ff}
    workers = db.execute(select(WorkerHeartbeat).where(WorkerHeartbeat.seen_at >= utcnow() - timedelta(seconds=90))).scalars().all()
    queues = sorted({q for w in workers for q in w.queues.split(",")})
    checks["workers"] = {"ok": bool(workers), "count": len(workers), "queues": queues}
    usage = shutil.disk_usage(get_settings().data_dir)
    checks["disk"] = {"ok": usage.free > 2 * 1024**3, "free_gb": round(usage.free / 1024**3, 1)}
    return checks


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/health/ready")
def ready(db: Session = Depends(get_db)):
    checks = _checks(db)
    ok = checks["database"]["ok"] and checks["ffmpeg"]["ok"]
    return JSONResponse({"ready": ok, "workers": checks["workers"]["ok"]}, status_code=200 if ok else 503)


@router.get("/diagnostics")
def diagnostics(db: Session = Depends(get_db), _: Principal = Depends(admin)) -> dict:
    from ...media.transcription import engines_status

    checks = _checks(db)
    checks["models"] = model_registry.status()
    checks["transcription_engines"] = engines_status()
    settings = get_settings()
    checks["config"] = {
        "env": settings.env,
        "storage_backend": settings.storage_backend,
        "google_app_configured": bool(settings.google_client_id and settings.google_client_secret),
        "youtube_api_key_configured": settings.youtube_api_key is not None,
        "meta_app_configured": bool(settings.meta_app_id and settings.meta_app_secret),
        "public_base_url": settings.public_base_url,
    }
    return checks
