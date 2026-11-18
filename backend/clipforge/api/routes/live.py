"""Live ingest sessions: authorised SRT feeds from the owner's encoder."""

from __future__ import annotations

import hashlib
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...db.base import get_db
from ...db.models import LiveSegment, LiveSession, Source
from ...ingestion.urls import detect
from ...jobs import queue
from ...media import live
from ...security.audit import audit
from ...security.crypto import read_secret, store_secret
from ...services import app_settings
from ..deps import Principal, admin, client_ip, editor, viewer

router = APIRouter(prefix="/live", tags=["live"])
ACTIVE = ("waiting", "live", "reconnecting", "stopping")


class NewSession(BaseModel):
    source_url: str | None = Field(None, max_length=2000)
    #: Minutes of raw stream kept for clipping; defaults to Settings → Storage clean-up.
    retention_minutes: int | None = Field(None, ge=5, le=1440)


def _summary(db: Session, s: LiveSession) -> dict:
    segments = db.execute(select(func.count()).select_from(LiveSegment).where(LiveSegment.session_id == s.id)).scalar_one()
    return {"id": str(s.id), "status": s.status, "protocol": s.ingest_protocol, "port": s.port,
            "source_id": str(s.source_id) if s.source_id else None, "connections": s.connections, "segments": segments,
            "stream_seconds": (s.stats or {}).get("stream_seconds", 0), "candidates": (s.stats or {}).get("candidates", []),
            "retention_minutes": s.retention_minutes, "started_at": s.started_at, "ended_at": s.ended_at,
            "created_at": s.created_at}


def _free_port(db: Session) -> int:
    used = {p for (p,) in db.execute(select(LiveSession.port).where(LiveSession.status.in_(ACTIVE))).all()}
    for port in live.port_range():
        if port not in used:
            return port
    raise HTTPException(409, {"code": "busy", "message": "All live ingest slots are in use. Stop a session first."})


@router.post("/sessions")
def create_session(body: NewSession, request: Request, db: Session = Depends(get_db),
                   principal: Principal = Depends(editor)) -> dict:
    session_id = uuid.uuid4()
    if body.source_url:
        info = detect(body.source_url)
        if not info.valid or info.platform != "youtube":
            raise HTTPException(400, {"code": "source", "message": "Link a YouTube live/video link, or leave it empty."})
        source = db.execute(select(Source).where(Source.platform == "youtube", Source.external_id == info.external_id)).scalar_one_or_none()
        if source is None:
            source = Source(original_url=body.source_url, normalized_url=info.normalized_url, platform="youtube",
                            source_type="livestream", external_id=info.external_id, owner_user_id=principal.id)
            db.add(source)
            db.flush()
    else:
        source = Source(platform="live_ingest", source_type="stream", external_id=str(session_id),
                        owner_user_id=principal.id, access_status="AUTHORIZED_LIVE_FEED")
        db.add(source)
        db.flush()
    passphrase = live.new_passphrase()
    secret = store_secret(db, f"live:{session_id}", passphrase)
    session = LiveSession(id=session_id, source_id=source.id, ingest_protocol="srt", port=_free_port(db),
                          stream_key_hash=hashlib.sha256(passphrase.encode()).hexdigest(), stream_key_hint=passphrase[-4:],
                          secret_id=secret.id, status="waiting",
                          retention_minutes=body.retention_minutes
                          or app_settings.get_section(db, "retention").live_buffer_minutes)
    db.add(session)
    db.flush()
    queue.enqueue(db, "live.ingest", {"session_id": str(session_id)}, dedupe_key=f"live:{session_id}", max_attempts=5,
                  status_label="Waiting for live source", user_id=principal.id)
    audit(db, "live.create", user_id=principal.id, target_type="live_session", target_id=session_id, ip=client_ip(request))
    return {**_summary(db, session), "publish_url": live.srt_publish_url(session.port, passphrase),
            "obs": {"service": "Custom…", "server": live.srt_publish_url(session.port, passphrase), "stream_key": "(leave empty)"},
            "note": "This URL contains a secret passphrase — treat it like a password."}


@router.get("/sessions")
def list_sessions(db: Session = Depends(get_db), _: Principal = Depends(viewer)) -> list[dict]:
    rows = db.execute(select(LiveSession).order_by(LiveSession.created_at.desc()).limit(50)).scalars()
    return [_summary(db, s) for s in rows]


@router.get("/sessions/{session_id}/credentials")
def credentials(session_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                principal: Principal = Depends(admin)) -> dict:
    session = db.get(LiveSession, session_id)
    if session is None or session.secret_id is None:
        raise HTTPException(404, {"code": "not_found", "message": "Session not found."})
    audit(db, "live.credentials_viewed", user_id=principal.id, target_type="live_session", target_id=session_id,
          ip=client_ip(request))
    return {"publish_url": live.srt_publish_url(session.port, read_secret(db, session.secret_id))}


@router.post("/sessions/{session_id}/stop")
def stop_session(session_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                 principal: Principal = Depends(editor)) -> dict:
    session = db.get(LiveSession, session_id)
    if session is None:
        raise HTTPException(404, {"code": "not_found", "message": "Session not found."})
    if session.status in ACTIVE:
        session.status = "stopping"
    audit(db, "live.stop", user_id=principal.id, target_type="live_session", target_id=session_id, ip=client_ip(request))
    return _summary(db, session)
