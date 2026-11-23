"""Local AI on this PC (AI Connections → Local AI): find Ollama / LM Studio, connect, download models."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.base import get_db
from ...db.models import Job
from ...jobs import queue
from ...providers import local_ai
from ...security.audit import audit
from ..deps import Principal, admin, client_ip, viewer

router = APIRouter(prefix="/providers/local", tags=["providers"])

Kind = Literal["ollama", "lmstudio", "jan", "llamacpp"]


class ConnectBody(BaseModel):
    kind: Kind = "ollama"


class PullBody(BaseModel):
    model: str = Field(..., min_length=1, max_length=120)


@router.get("")
async def status(db: Session = Depends(get_db), _: Principal = Depends(viewer)) -> dict:
    servers = await run_in_threadpool(local_ai.detect)
    for found in servers:
        conn = local_ai.find_connection(db, found["kind"])
        found["connection"] = ({"id": str(conn.id), "enabled": conn.enabled, "status": conn.status,
                                "models": sorted(m.model_id for m in conn.models if m.enabled)} if conn else None)
    pulling = db.execute(select(Job).where(Job.type == "ai.pull_model", Job.status.in_(queue.ACTIVE))
                         .order_by(Job.created_at.desc())).scalars().first()
    return {"servers": servers, "recommended": list(local_ai.RECOMMENDED), "install_url": local_ai.OLLAMA_DOWNLOAD_URL,
            "chat_ready": local_ai.chat_model_ready(db),
            "pulling": {**queue.job_summary(pulling), "model": pulling.payload.get("model")} if pulling else None}


@router.post("/connect")
def connect(body: ConnectBody, request: Request, db: Session = Depends(get_db), principal: Principal = Depends(admin)) -> dict:
    found = local_ai._probe(local_ai.server(body.kind), 1.5)
    if not found["running"]:
        raise HTTPException(409, {"code": "not_running", "message": (
            f"{found['label']} is not answering on {found['url']}. Start it (for Ollama: open the Ollama app) and try again.")})
    conn, report = local_ai.connect(db, body.kind)
    audit(db, "provider.local_connect", user_id=principal.id, target_type="provider", target_id=conn.id,
          details={"kind": body.kind, "models": len(found["models"])}, ip=client_ip(request))
    return {"connection_id": str(conn.id), "test": report, "models": sorted(m.model_id for m in conn.models)}


@router.post("/pull")
def pull(body: PullBody, request: Request, db: Session = Depends(get_db), principal: Principal = Depends(admin)) -> dict:
    name = body.model.strip()
    if not local_ai.valid_model_name(name):
        raise HTTPException(400, {"code": "model", "message": "That is not a valid model name (for example qwen2.5:3b)."})
    found = local_ai._probe(local_ai.server("ollama"), 1.5)
    if not found["running"]:
        raise HTTPException(409, {"code": "not_running", "message": (
            "Ollama is not running on this PC. Install it from ollama.com (free), open it, then try again.")})
    job = queue.enqueue(db, "ai.pull_model", {"model": name}, queue="default", user_id=principal.id,
                        dedupe_key=f"ai.pull_model:{name}", status_label="Downloading the AI model")
    audit(db, "provider.local_pull", user_id=principal.id, target_type="job", target_id=job.id, details={"model": name},
          ip=client_ip(request))
    return queue.job_summary(job)
