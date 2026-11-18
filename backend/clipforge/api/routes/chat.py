"""Brain Chat: conversations, messages and optional media uploads."""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...brain.chat import handle_message
from ...config import get_settings
from ...db.base import get_db
from ...db.models import Conversation, MediaAsset, Message
from ...security.audit import audit
from ...storage import get_storage, looks_like_media
from ..deps import Principal, client_ip, editor, viewer

router = APIRouter(prefix="/chat", tags=["chat"])

ALLOWED_UPLOAD_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}


class NewConversation(BaseModel):
    title: str | None = Field(None, max_length=200)


class NewMessage(BaseModel):
    text: str = Field("", max_length=8000)
    attachments: list[uuid.UUID] = Field(default_factory=list, max_length=5)


def _own(db: Session, conversation_id: uuid.UUID, principal: Principal) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != principal.id:
        raise HTTPException(404, {"code": "not_found", "message": "Conversation not found."})
    return conversation


def _message(m: Message) -> dict:
    return {"id": str(m.id), "role": m.role, "content": m.content, "meta": m.meta, "created_at": m.created_at}


@router.get("/conversations")
def list_conversations(db: Session = Depends(get_db), principal: Principal = Depends(viewer)) -> list[dict]:
    rows = db.execute(select(Conversation).where(Conversation.user_id == principal.id)
                      .order_by(Conversation.updated_at.desc()).limit(100)).scalars()
    return [{"id": str(c.id), "title": c.title, "updated_at": c.updated_at} for c in rows]


@router.post("/conversations")
def create_conversation(body: NewConversation, db: Session = Depends(get_db), principal: Principal = Depends(viewer)) -> dict:
    conversation = Conversation(user_id=principal.id, title=body.title or "New conversation")
    db.add(conversation)
    db.flush()
    return {"id": str(conversation.id), "title": conversation.title, "updated_at": conversation.updated_at}


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: uuid.UUID, db: Session = Depends(get_db), principal: Principal = Depends(viewer)) -> dict:
    db.delete(_own(db, conversation_id, principal))
    return {"ok": True}


@router.get("/conversations/{conversation_id}/messages")
def list_messages(conversation_id: uuid.UUID, db: Session = Depends(get_db), principal: Principal = Depends(viewer)) -> list[dict]:
    _own(db, conversation_id, principal)
    rows = db.execute(select(Message).where(Message.conversation_id == conversation_id)
                      .order_by(Message.created_at).limit(500)).scalars()
    return [_message(m) for m in rows]


@router.post("/conversations/{conversation_id}/messages")
def send_message(conversation_id: uuid.UUID, body: NewMessage, db: Session = Depends(get_db),
                 principal: Principal = Depends(viewer)) -> dict:
    _own(db, conversation_id, principal)
    if not body.text.strip() and not body.attachments:
        raise HTTPException(400, {"code": "empty", "message": "Type a message or attach a file."})
    for asset_id in body.attachments:
        asset = db.get(MediaAsset, asset_id)
        if asset is None or asset.kind != "upload":
            raise HTTPException(400, {"code": "attachment", "message": "Attachment not found."})
    db.commit()  # the Brain opens its own transactions
    try:
        return handle_message(conversation_id, principal.user, body.text, [str(a) for a in body.attachments])
    except LookupError as exc:
        raise HTTPException(404, {"code": "not_found", "message": str(exc)}) from exc


@router.post("/upload")
def upload(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db),
           principal: Principal = Depends(editor)) -> dict:
    """Optional fallback input: upload a media file you have rights to.

    A plain (sync) endpoint on purpose: FastAPI runs it in its threadpool, so
    copying a multi-gigabyte file never blocks the event loop.
    """
    settings = get_settings()
    name = Path(file.filename or "upload").name[:200]
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXT:
        raise HTTPException(400, {"code": "type", "message": "Upload a video or audio file (mp4, mov, webm, mkv, mp3, wav, m4a…)."})
    limit = settings.max_upload_mb * 1024 * 1024
    digest = hashlib.sha256()
    size = 0
    tmp_dir = Path(tempfile.mkdtemp(dir=settings.work_dir, prefix="upload-"))
    tmp = tmp_dir / f"incoming{ext}"
    try:
        with open(tmp, "wb") as handle:
            while chunk := file.file.read(1 << 20):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, {"code": "too_large", "message": f"Files up to {settings.max_upload_mb} MB are accepted."})
                digest.update(chunk)
                handle.write(chunk)
        if size == 0 or not looks_like_media(tmp):
            raise HTTPException(400, {"code": "not_media", "message": "That file doesn't look like a video or audio file."})
    except HTTPException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    sha = digest.hexdigest()
    existing = db.execute(select(MediaAsset).where(MediaAsset.sha256 == sha, MediaAsset.kind == "upload")).scalars().first()
    if existing is not None:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return {"asset_id": str(existing.id), "filename": existing.info.get("filename"), "size": existing.size_bytes, "duplicate": True}
    key = f"uploads/{sha[:2]}/{sha}{ext}"
    stored = get_storage().put_file(tmp, key, move=True)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    asset = MediaAsset(kind="upload", storage_key=key, mime=file.content_type or "application/octet-stream",
                       size_bytes=stored, sha256=sha, info={"filename": name, "validated": False})
    db.add(asset)
    db.flush()
    audit(db, "media.upload", user_id=principal.id, target_type="media_asset", target_id=asset.id,
          details={"size": stored, "filename": name}, ip=client_ip(request))
    return {"asset_id": str(asset.id), "filename": name, "size": stored, "duplicate": False}
