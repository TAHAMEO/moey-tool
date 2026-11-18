"""Permanent memory and cross-model knowledge: view, correct, delete, export."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db.base import get_db
from ...db.models import KnowledgeItem
from ...knowledge import engine as knowledge
from ...memory.service import get_memory
from ...providers.base import NoProviderAvailable
from ...security.audit import audit
from ..deps import Principal, client_ip, editor, viewer

router = APIRouter(tags=["memory"])


class MemoryIn(BaseModel):
    text: str = Field(min_length=2, max_length=2000)


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    important: bool = False


class Verification(BaseModel):
    status: Literal["verified", "rejected", "single_source", "consensus", "disputed"]
    note: str = Field("", max_length=500)


def _scope(scope: str, principal: Principal) -> dict:
    return {"agent": True} if scope == "brain" else {"user_id": str(principal.id)}


@router.get("/memory")
def list_memories(scope: Literal["user", "brain"] = "user", principal: Principal = Depends(viewer)) -> list[dict]:
    return get_memory().list(**_scope(scope, principal))


@router.get("/memory/search")
def search(q: str, scope: Literal["user", "brain"] = "user", principal: Principal = Depends(viewer)) -> list[dict]:
    return get_memory().recall(q, limit=10, min_score=0.2, **_scope(scope, principal))


@router.post("/memory")
def add_memory(body: MemoryIn, request: Request, db: Session = Depends(get_db), principal: Principal = Depends(editor)) -> list[dict]:
    events = get_memory().remember(body.text, user_id=str(principal.id), category="manual")
    audit(db, "memory.add", user_id=principal.id, details={"events": len(events)}, ip=client_ip(request))
    return events


@router.patch("/memory/{memory_id}")
def correct_memory(memory_id: str, body: MemoryIn, request: Request, db: Session = Depends(get_db),
                   principal: Principal = Depends(editor)) -> dict:
    result = get_memory().update(memory_id, body.text)
    audit(db, "memory.correct", user_id=principal.id, target_type="memory", target_id=memory_id, ip=client_ip(request))
    return result if isinstance(result, dict) else {"ok": True}


@router.delete("/memory/{memory_id}")
def delete_memory(memory_id: str, request: Request, db: Session = Depends(get_db), principal: Principal = Depends(editor)) -> dict:
    get_memory().delete(memory_id)
    audit(db, "memory.delete", user_id=principal.id, target_type="memory", target_id=memory_id, ip=client_ip(request))
    return {"ok": True}


@router.get("/memory/{memory_id}/history")
def memory_history(memory_id: str, _: Principal = Depends(viewer)) -> list[dict]:
    return get_memory().history(memory_id)


@router.get("/memory-export")
def export_memory(principal: Principal = Depends(viewer)):
    return JSONResponse(get_memory().export(user_id=str(principal.id)),
                        headers={"Content-Disposition": 'attachment; filename="clipforge-memory.json"'})


@router.get("/knowledge")
def list_knowledge(db: Session = Depends(get_db), _: Principal = Depends(viewer)) -> list[dict]:
    rows = db.execute(select(KnowledgeItem).order_by(KnowledgeItem.updated_at.desc()).limit(200)).scalars()
    return [{"id": str(k.id), "topic": k.topic, "question": k.question, "answer": k.answer, "providers": k.providers,
             "agreement": k.agreement, "disagreements": k.disagreements, "confidence": k.confidence,
             "verification_status": k.verification_status, "reuse_count": k.reuse_count, "responses": k.responses,
             "created_at": k.created_at} for k in rows]


@router.post("/knowledge/ask")
def ask(body: Question, principal: Principal = Depends(editor)) -> dict:
    try:
        return knowledge.ask(body.question, important=body.important).as_dict()
    except NoProviderAvailable as exc:
        raise HTTPException(503, {"code": "no_provider", "message": str(exc)}) from exc


@router.patch("/knowledge/{item_id}")
def verify(item_id: uuid.UUID, body: Verification, request: Request, db: Session = Depends(get_db),
           principal: Principal = Depends(editor)) -> dict:
    try:
        item = knowledge.set_verification(db, item_id, body.status, note=body.note)
    except LookupError as exc:
        raise HTTPException(404, {"code": "not_found", "message": str(exc)}) from exc
    audit(db, "knowledge.verify", user_id=principal.id, target_type="knowledge", target_id=item_id,
          details={"status": body.status}, ip=client_ip(request))
    return {"id": str(item.id), "verification_status": item.verification_status, "confidence": item.confidence}
