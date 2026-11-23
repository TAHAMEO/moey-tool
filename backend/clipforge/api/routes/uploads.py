"""Resumable uploads in pieces (see services/chunked_uploads.py).

    POST   /uploads                 {filename, size, mime, purpose}  -> {upload_id, chunk_size, received}
    PUT    /uploads/{id}?offset=N   the next piece as the raw request body (at most chunk_size bytes)
    GET    /uploads/{id}            how much has arrived (to continue after a lost connection)
    DELETE /uploads/{id}            cancel

A finished upload is turned into a file with ``POST /chat/upload/complete`` (videos and audio) or
``POST /library/from-upload`` (media library items).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from ...config import get_settings
from ...services import chunked_uploads as cu
from ..deps import Principal, editor

router = APIRouter(prefix="/uploads", tags=["uploads"])

#: Largest library item (music, logos, B-roll, masters); videos use CLIPFORGE_MAX_UPLOAD_MB.
LIBRARY_MAX_BYTES = 1024 * 1024 * 1024


class NewUpload(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)
    size: int = Field(..., gt=0)
    mime: str | None = Field(None, max_length=120)
    purpose: Literal["media", "library"] = "media"


def http_error(exc: cu.UploadError) -> HTTPException:
    detail = {"code": exc.code, "message": str(exc)}
    if exc.received is not None:
        detail["received"] = exc.received
    return HTTPException(exc.status, detail)


@router.post("")
def start_upload(body: NewUpload, principal: Principal = Depends(editor)) -> dict:
    if body.purpose == "media":
        from .chat import ALLOWED_UPLOAD_EXT

        if Path(body.filename).suffix.lower() not in ALLOWED_UPLOAD_EXT:
            raise HTTPException(400, {"code": "type",
                                      "message": "Upload a video or audio file (mp4, mov, webm, mkv, mp3, wav, m4a…)."})
        limit = get_settings().max_upload_mb * 1024 * 1024
    else:
        limit = LIBRARY_MAX_BYTES
    try:
        return cu.create(str(principal.id), body.filename, body.size, body.mime, body.purpose, limit).to_dict()
    except cu.UploadError as exc:
        raise http_error(exc) from exc


@router.get("/{upload_id}")
def upload_status(upload_id: str, principal: Principal = Depends(editor)) -> dict:
    try:
        return cu.get(upload_id, str(principal.id)).to_dict()
    except cu.UploadError as exc:
        raise http_error(exc) from exc


@router.put("/{upload_id}")
async def upload_piece(upload_id: str, offset: int, request: Request, principal: Principal = Depends(editor)) -> dict:
    declared = int(request.headers.get("content-length") or 0)
    if declared > cu.CHUNK_BYTES:
        raise HTTPException(413, {"code": "chunk_too_large", "message": f"Pieces are limited to {cu.CHUNK_BYTES >> 20} MB."})
    chunk = bytearray()
    async for part in request.stream():
        chunk += part
        if len(chunk) > cu.CHUNK_BYTES:
            raise HTTPException(413, {"code": "chunk_too_large",
                                      "message": f"Pieces are limited to {cu.CHUNK_BYTES >> 20} MB."})
    if not chunk:
        raise HTTPException(400, {"code": "empty", "message": "The piece was empty."})
    try:
        upload = await run_in_threadpool(cu.append, upload_id, str(principal.id), offset, bytes(chunk))
    except cu.UploadError as exc:
        raise http_error(exc) from exc
    return upload.to_dict()


@router.delete("/{upload_id}")
def cancel_upload(upload_id: str, principal: Principal = Depends(editor)) -> dict:
    try:
        cu.cancel(upload_id, str(principal.id))
    except cu.UploadError as exc:
        raise http_error(exc) from exc
    return {"ok": True}
