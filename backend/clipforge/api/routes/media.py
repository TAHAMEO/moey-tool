"""Authenticated media delivery (Range requests supported for video seeking)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ...db.base import get_db
from ...db.models import MediaAsset
from ...storage import StorageError, get_storage
from ..deps import Principal, viewer

router = APIRouter(tags=["media"])

SERVABLE = {"clip", "thumbnail", "subtitle", "transcript", "upload", "library"}


@router.get("/media/{asset_id}")
def media(asset_id: uuid.UUID, download: bool = False, db: Session = Depends(get_db), _: Principal = Depends(viewer)):
    asset = db.get(MediaAsset, asset_id)
    if asset is None or asset.kind not in SERVABLE or (asset.info or {}).get("deleted"):
        raise HTTPException(404, {"code": "not_found", "message": "File not found."})
    try:
        path = get_storage().local_path(asset.storage_key)
    except StorageError as exc:
        raise HTTPException(404, {"code": "not_found", "message": "File is no longer stored."}) from exc
    headers = {"Cache-Control": "private, max-age=3600", "Content-Security-Policy": "default-src 'none'; sandbox"}
    return FileResponse(path, media_type=asset.mime, filename=path.name if download else None,
                        content_disposition_type="attachment" if download else "inline", headers=headers)
