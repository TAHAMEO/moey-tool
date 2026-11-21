"""Batches: process every video of a folder with one recipe (My Videos → Process a folder)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...batches import service
from ...batches.recipe import Recipe
from ...db.base import get_db
from ...db.models import Batch, MediaAsset, WatchFolder
from ...jobs import queue
from ...security.audit import audit
from ..deps import Principal, client_ip, editor, viewer

router = APIRouter(tags=["batches"])


class NewBatch(BaseModel):
    name: str = Field("", max_length=200)
    recipe: dict = Field(default_factory=dict)


class NewItem(BaseModel):
    asset_id: uuid.UUID
    filename: str = Field("", max_length=1000)


class NewFolder(BaseModel):
    path: str = Field("", max_length=1000)  # inside the inbox; "" = the inbox itself
    name: str | None = Field(None, max_length=200)
    recipe: dict = Field(default_factory=dict)
    include_subfolders: bool = True
    include_existing: bool = True
    after_processing: str = Field("keep", pattern="^(keep|move)$")


class FolderChange(BaseModel):
    enabled: bool | None = None
    recipe: dict | None = None
    include_subfolders: bool | None = None
    after_processing: str | None = Field(None, pattern="^(keep|move)$")


def _recipe(data: dict) -> Recipe:
    try:
        return Recipe(**data)
    except ValidationError as exc:
        error = exc.errors(include_url=False)[0]
        where = ".".join(str(p) for p in error.get("loc", ()))
        raise HTTPException(422, {"code": "recipe", "message": f"{where}: {error.get('msg')}" if where else error.get("msg")}) from exc


def _batch(db: Session, batch_id: uuid.UUID) -> Batch:
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise HTTPException(404, {"code": "not_found", "message": "Batch not found."})
    return batch


# ------------------------------------------------------------------ batches
@router.get("/batches")
def list_batches(db: Session = Depends(get_db), _: Principal = Depends(viewer)) -> list[dict]:
    batches = db.execute(select(Batch).where(Batch.status != "cancelled").order_by(Batch.created_at.desc()).limit(50)).scalars()
    return [service.summary(db, batch) for batch in batches]


@router.post("/batches")
def create_batch(body: NewBatch, request: Request, db: Session = Depends(get_db), principal: Principal = Depends(editor)) -> dict:
    """A batch for videos picked in the browser; add each one with POST /batches/{id}/items as it finishes uploading."""
    recipe = _recipe(body.recipe)
    batch = service.create_batch(db, name=body.name or "Folder upload", recipe=recipe, origin="upload", user_id=principal.id)
    audit(db, "batch.create", user_id=principal.id, target_type="batch", target_id=batch.id,
          details={"recipe": recipe.summary()}, ip=client_ip(request))
    return service.summary(db, batch)


@router.get("/batches/{batch_id}")
def get_batch(batch_id: uuid.UUID, db: Session = Depends(get_db), _: Principal = Depends(viewer)) -> dict:
    return service.summary(db, _batch(db, batch_id), items=True)


@router.post("/batches/{batch_id}/items")
def add_item(batch_id: uuid.UUID, body: NewItem, db: Session = Depends(get_db), _: Principal = Depends(editor)) -> dict:
    batch = _batch(db, batch_id)
    asset = db.get(MediaAsset, body.asset_id)
    if asset is None or asset.kind != "upload":
        raise HTTPException(400, {"code": "asset", "message": "Upload the file first."})
    try:
        item = service.add_upload(db, batch, asset, body.filename)
    except service.BatchError as exc:
        raise HTTPException(409, {"code": "batch", "message": str(exc)}) from exc
    return {"id": str(item.id), "status": item.status, "project_id": str(item.project_id) if item.project_id else None,
            "error": item.error}


@router.post("/batches/{batch_id}/{action}")
def batch_action(batch_id: uuid.UUID, action: str, request: Request, db: Session = Depends(get_db),
                 principal: Principal = Depends(editor)) -> dict:
    batch = _batch(db, batch_id)
    if action == "close":
        service.close_batch(db, batch)
    elif action == "pause":
        service.pause_batch(db, batch)
    elif action == "resume":
        service.resume_batch(db, batch)
    elif action == "cancel":
        service.cancel_batch(db, batch)
    else:
        raise HTTPException(400, {"code": "invalid", "message": "Unknown action."})
    audit(db, f"batch.{action}", user_id=principal.id, target_type="batch", target_id=batch_id, ip=client_ip(request))
    db.flush()
    return service.summary(db, batch)


# ---------------------------------------------------------------- folders
@router.get("/folders")
def browse_folders(path: str = "", db: Session = Depends(get_db), _: Principal = Depends(viewer)) -> dict:
    """The inbox and its sub-folders (folders CLIPFORGE can watch), plus the folders already watched."""
    try:
        listing = service.browse(path)
    except service.BatchError as exc:
        raise HTTPException(400, {"code": "folder", "message": str(exc)}) from exc
    watched = db.execute(select(WatchFolder).order_by(WatchFolder.created_at)).scalars().all()
    listing["watched"] = [service._folder_out(f) for f in watched]
    return listing


@router.post("/folders")
def watch_folder(body: NewFolder, request: Request, db: Session = Depends(get_db), principal: Principal = Depends(editor)) -> dict:
    recipe = _recipe(body.recipe)
    try:
        folder = service.create_watch_folder(db, path=body.path, recipe=recipe, name=body.name, user_id=principal.id,
                                             include_subfolders=body.include_subfolders,
                                             after_processing=body.after_processing, include_existing=body.include_existing)
    except service.BatchError as exc:
        raise HTTPException(409, {"code": "folder", "message": str(exc)}) from exc
    queue.enqueue(db, "folders.scan", {}, dedupe_key="folders-scan-now", priority=60)
    audit(db, "folder.watch", user_id=principal.id, target_type="watch_folder", target_id=folder.id,
          details={"path": folder.path, "recipe": recipe.summary()}, ip=client_ip(request))
    return service.summary(db, db.get(Batch, folder.batch_id))


@router.patch("/folders/{folder_id}")
def change_folder(folder_id: uuid.UUID, body: FolderChange, request: Request, db: Session = Depends(get_db),
                  principal: Principal = Depends(editor)) -> dict:
    folder = db.get(WatchFolder, folder_id)
    if folder is None:
        raise HTTPException(404, {"code": "not_found", "message": "Folder not found."})
    batch = db.get(Batch, folder.batch_id) if folder.batch_id else None
    if body.recipe is not None:
        recipe = _recipe(body.recipe)
        folder.recipe = recipe.model_dump(mode="json")
        if batch is not None:
            batch.recipe = folder.recipe  # applies to videos found from now on
    if body.enabled is not None:
        folder.enabled = body.enabled
        if batch is not None and batch.status == "cancelled" and body.enabled:
            batch.status = "running"
    if body.include_subfolders is not None:
        folder.include_subfolders = body.include_subfolders
    if body.after_processing is not None:
        folder.after_processing = body.after_processing
    if folder.enabled:
        queue.enqueue(db, "folders.scan", {}, dedupe_key="folders-scan-now", priority=60)
    audit(db, "folder.change", user_id=principal.id, target_type="watch_folder", target_id=folder_id,
          details=body.model_dump(exclude_none=True), ip=client_ip(request))
    return service._folder_out(folder)


@router.delete("/folders/{folder_id}")
def stop_watching(folder_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                  principal: Principal = Depends(editor)) -> dict:
    """Stop watching; the videos already processed and their projects stay."""
    folder = db.get(WatchFolder, folder_id)
    if folder is None:
        raise HTTPException(404, {"code": "not_found", "message": "Folder not found."})
    batch = db.get(Batch, folder.batch_id) if folder.batch_id else None
    if batch is not None and batch.status in ("open", "running", "paused"):
        batch.status = "completed"
        batch.watch_folder_id = None
    db.delete(folder)
    audit(db, "folder.unwatch", user_id=principal.id, target_type="watch_folder", target_id=folder_id, ip=client_ip(request))
    return {"ok": True}
