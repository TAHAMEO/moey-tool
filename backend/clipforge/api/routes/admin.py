"""Backups, data export (portability) and data deletion."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...db.base import get_db, utcnow
from ...db.models import (
    Clip,
    Conversation,
    KnowledgeItem,
    LearningRecord,
    MediaAsset,
    MetricSnapshot,
    OutputPackage,
    Project,
    Publication,
    Source,
    Strategy,
)
from ...security.audit import audit
from ...services import app_settings
from ...storage import get_storage
from ..deps import Principal, admin, client_ip, owner

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


class DeleteAll(BaseModel):
    confirm: str


def _rows(db: Session, model, fields: list[str]) -> list[dict[str, Any]]:
    return [{f: getattr(r, f) for f in fields} for r in db.execute(select(model)).scalars()]


@router.post("/backup")
def backup_now(request: Request, db: Session = Depends(get_db), principal: Principal = Depends(admin)) -> dict:
    from ...handlers.maintenance_jobs import run_backup

    try:
        manifest = run_backup("manual")
    except RuntimeError as exc:
        raise HTTPException(500, {"code": "backup", "message": str(exc)}) from exc
    audit(db, "admin.backup", user_id=principal.id, details={"backup": manifest.get("created_at")}, ip=client_ip(request))
    return manifest


@router.get("/backups")
def list_backups(_: Principal = Depends(admin)) -> list[dict]:
    root = get_settings().backups_dir
    out = []
    for path in sorted((p for p in root.iterdir() if p.is_dir()), reverse=True):
        manifest = path / "manifest.json"
        if manifest.exists():
            out.append({"name": path.name, **json.loads(manifest.read_text())})
    return out


@router.get("/export")
def export_data(request: Request, db: Session = Depends(get_db), principal: Principal = Depends(admin)):
    data = {
        "exported_at": utcnow().isoformat(),
        "settings": app_settings.get_all(db),
        "sources": _rows(db, Source, ["id", "platform", "normalized_url", "external_id", "access_status", "source_metadata", "created_at"]),
        "projects": _rows(db, Project, ["id", "title", "instruction", "request", "status", "analysis_level", "limitations", "created_at"]),
        "clips": _rows(db, Clip, ["id", "project_id", "rank", "start_s", "end_s", "title", "hook", "transcript_text", "scores",
                                  "total_score", "reasons", "strategy_id", "experiment_variant"]),
        "packages": _rows(db, OutputPackage, ["id", "clip_id", "platform", "title", "description", "tags", "caption", "hashtags", "status"]),
        "publications": _rows(db, Publication, ["id", "package_id", "platform", "status", "external_id", "external_url", "published_at"]),
        "metrics": _rows(db, MetricSnapshot, ["publication_id", "platform", "collected_at", "age_hours", "metrics", "unavailable"]),
        "learning_records": _rows(db, LearningRecord, ["id", "kind", "finding", "evidence", "platform", "confidence",
                                                       "limitations", "causal_claim", "status", "created_at"]),
        "strategies": _rows(db, Strategy, ["id", "version", "name", "status", "params", "rationale", "created_at"]),
        "knowledge": _rows(db, KnowledgeItem, ["id", "question", "answer", "providers", "confidence", "verification_status"]),
    }
    try:
        from ...memory.service import get_memory

        data["memory"] = get_memory().export(user_id=str(principal.id))
    except Exception as exc:  # noqa: BLE001 - the export still contains everything else
        data["memory_error"] = str(exc)[:200]
    audit(db, "admin.export", user_id=principal.id, ip=client_ip(request))
    return JSONResponse(json.loads(json.dumps(data, default=str)),
                        headers={"Content-Disposition": 'attachment; filename="clipforge-export.json"'})


@router.post("/delete-all-data")
def delete_all(body: DeleteAll, request: Request, db: Session = Depends(get_db), principal: Principal = Depends(owner)) -> dict:
    """Owner-only: removes all content data (projects, media, analytics, memory). Accounts/settings remain."""
    if body.confirm != "DELETE ALL DATA":
        raise HTTPException(400, {"code": "confirm", "message": "Type DELETE ALL DATA to confirm."})
    storage = get_storage()
    warnings: list[str] = []
    for asset in db.execute(select(MediaAsset)).scalars():
        try:
            storage.delete(asset.storage_key)
        except OSError as exc:
            log.warning("Could not delete %s: %s", asset.storage_key, exc)
            warnings.append(f"file {asset.storage_key} could not be deleted")
    for model in (MetricSnapshot, Publication, OutputPackage, Project, Source, KnowledgeItem, LearningRecord, Conversation):
        db.execute(delete(model))
    db.execute(delete(MediaAsset))
    try:
        from ...memory.service import get_memory

        get_memory().delete_all(user_id=str(principal.id))
        get_memory().delete_all(agent=True)
    except Exception as exc:  # noqa: BLE001 - reported to the owner below, never hidden
        log.error("Memory deletion failed: %s", exc)
        warnings.append(f"memory could not be deleted ({exc.__class__.__name__}); try again")
    audit(db, "admin.delete_all_data", user_id=principal.id, details={"warnings": warnings}, ip=client_ip(request))
    return {"ok": not warnings, "warnings": warnings}
