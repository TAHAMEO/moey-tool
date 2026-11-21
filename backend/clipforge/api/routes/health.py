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
    # Transcription runs in the media workers (possibly on other machines): report what *they* have.
    media_engines = sorted({e for w in workers if "media" in w.queues.split(",")
                            for e in (w.capabilities or {}).get("transcription", [])})
    checks["workers"] = {"ok": bool(workers), "count": len(workers), "queues": queues,
                         "media_transcription_engines": media_engines}
    usage = shutil.disk_usage(get_settings().data_dir)
    checks["disk"] = {"ok": usage.free > 2 * 1024**3, "free_gb": round(usage.free / 1024**3, 1)}
    return checks


def memory_check() -> dict:
    """Permanent memory is used best-effort by the chat, so problems must be visible here."""
    try:
        from ...memory.service import get_memory

        get_memory().list(user_id="__healthcheck__", limit=1)
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"[:300]}


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
    checks["memory"] = memory_check()
    from ...ingestion import public_download

    checks["downloader"] = public_download.available()
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
