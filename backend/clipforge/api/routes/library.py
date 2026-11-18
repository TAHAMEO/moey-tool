"""Owner media library: licensed music, sound effects, B-roll, logos, master files.

Only items whose rights the owner explicitly confirms are ever used in edits.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...db.base import get_db
from ...db.models import LibraryItem, MediaAsset
from ...security.audit import audit
from ...storage import get_storage, looks_like_media
from ..deps import Principal, client_ip, editor, viewer

router = APIRouter(prefix="/library", tags=["library"])
KINDS = {"music": {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}, "sfx": {".mp3", ".m4a", ".wav", ".ogg"},
         "broll": {".mp4", ".mov", ".webm", ".mkv"}, "logo": {".png"}, "master": {".mp4", ".mov", ".mkv", ".webm", ".m4v"}}


@router.get("")
def list_items(db: Session = Depends(get_db), _: Principal = Depends(viewer)) -> list[dict]:
    rows = db.execute(select(LibraryItem).order_by(LibraryItem.created_at.desc())).scalars()
    return [{"id": str(i.id), "kind": i.kind, "title": i.title, "tags": i.tags, "license_note": i.license_note,
             "rights_confirmed": i.rights_confirmed, "url": f"/api/media/{i.asset_id}" if i.asset_id else None,
             "created_at": i.created_at} for i in rows]


@router.post("")
def add_item(request: Request, file: UploadFile = File(...), kind: str = Form(...), title: str = Form(""),
             license_note: str = Form(""), rights_confirmed: bool = Form(False), tags: str = Form(""),
             db: Session = Depends(get_db), principal: Principal = Depends(editor)) -> dict:
    # Sync on purpose: FastAPI runs it in the threadpool, keeping file I/O off the event loop.
    if kind not in KINDS:
        raise HTTPException(400, {"code": "kind", "message": "Kind must be music, sfx, broll, logo or master."})
    if not rights_confirmed:
        raise HTTPException(400, {"code": "rights", "message": "Confirm you hold the rights/licence to use this file."})
    name = Path(file.filename or "file").name[:200]
    ext = Path(name).suffix.lower()
    if ext not in KINDS[kind]:
        raise HTTPException(400, {"code": "type", "message": f"Accepted types for {kind}: {', '.join(sorted(KINDS[kind]))}"})
    settings = get_settings()
    tmp_dir = Path(tempfile.mkdtemp(dir=settings.work_dir, prefix="library-"))
    tmp = tmp_dir / f"item{ext}"
    digest, size = hashlib.sha256(), 0
    try:
        with open(tmp, "wb") as handle:
            while chunk := file.file.read(1 << 20):
                size += len(chunk)
                if size > 1024 * 1024 * 1024:
                    raise HTTPException(413, {"code": "too_large", "message": "Library files are limited to 1 GB."})
                digest.update(chunk)
                handle.write(chunk)
        if kind == "logo":
            with open(tmp, "rb") as handle:
                if handle.read(8) != b"\x89PNG\r\n\x1a\n":
                    raise HTTPException(400, {"code": "type", "message": "Logos must be PNG images."})
        elif not looks_like_media(tmp):
            raise HTTPException(400, {"code": "type", "message": "That file doesn't look like audio or video."})
    except HTTPException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    item_id = uuid.uuid4()
    key = f"library/{kind}/{item_id}{ext}"
    get_storage().put_file(tmp, key, move=True)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    if kind == "master":  # masters are also matched against the owner's YouTube videos
        masters = settings.library_dir / "masters"
        masters.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(get_storage().local_path(key), masters / name)  # streamed, never loaded into memory
    asset = MediaAsset(kind="library", storage_key=key, mime=file.content_type or "application/octet-stream",
                       size_bytes=size, sha256=digest.hexdigest(), info={"filename": name})
    db.add(asset)
    db.flush()
    item = LibraryItem(id=item_id, kind=kind, title=title or Path(name).stem, asset_id=asset.id,
                       tags=[t.strip() for t in tags.split(",") if t.strip()][:20], license_note=license_note[:2000],
                       rights_confirmed=True)
    db.add(item)
    audit(db, "library.add", user_id=principal.id, target_type="library_item", target_id=item_id,
          details={"kind": kind, "license_note": license_note[:200]}, ip=client_ip(request))
    return {"id": str(item_id), "kind": kind, "title": item.title}


@router.delete("/{item_id}")
def delete_item(item_id: uuid.UUID, request: Request, db: Session = Depends(get_db), principal: Principal = Depends(editor)) -> dict:
    item = db.get(LibraryItem, item_id)
    if item is None:
        raise HTTPException(404, {"code": "not_found", "message": "Item not found."})
    asset = db.get(MediaAsset, item.asset_id) if item.asset_id else None
    if asset is not None:
        if item.kind == "master" and (filename := (asset.info or {}).get("filename")):
            (get_settings().library_dir / "masters" / Path(filename).name).unlink(missing_ok=True)
        get_storage().delete(asset.storage_key)
        db.delete(asset)
    db.delete(item)
    audit(db, "library.delete", user_id=principal.id, target_type="library_item", target_id=item_id, ip=client_ip(request))
    return {"ok": True}
