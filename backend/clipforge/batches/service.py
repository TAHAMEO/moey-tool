"""Batches and watched folders.

A *batch* is a set of videos processed with one :class:`~.recipe.Recipe`.
Videos arrive in two ways:

* **Picked in the browser** — the owner selects a folder; the web app uploads
  every video in it and adds each one to the batch as soon as it has arrived.
* **Watched folder** — a folder under the inbox root (on Windows:
  ``Videos\\ClipForge``). Every minute CLIPFORGE looks for new videos; a file is
  taken once its size and modification time have stopped changing (so a copy
  in progress is never read half-written). The owner's files are never
  modified unless they ask for processed files to be moved to ``Processed``.

Every video becomes an ordinary project (the same workflow as a chat request),
so progress, approvals, retries and publishing work exactly as they do there.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import uuid
from datetime import UTC, datetime, time, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.base import utcnow
from ..db.models import Batch, BatchItem, Clip, MediaAsset, OutputPackage, Project, Source, WatchFolder
from ..jobs import queue
from .recipe import Recipe

log = logging.getLogger(__name__)

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".mts", ".m2ts", ".3gp", ".wmv", ".flv"}
SKIP_DIRS = {"processed", "@eadir", "$recycle.bin", "system volume information", ".trash", ".trashes"}
STABLE_AFTER_S = 120  # a file untouched this long is complete even on the first look
MAX_NEW_PER_SCAN = 50
BATCH_PRIORITY = 110  # just behind interactive chat requests (100)
ACTIVE_PROJECT = ("queued", "running", "waiting", "publishing", "created")


class BatchError(ValueError):
    pass


# ------------------------------------------------------------------ folders
def inbox_root() -> Path:
    return get_settings().inbox.resolve()


def resolve_folder(relative: str) -> Path:
    """A folder inside the inbox root; anything that would leave it is refused."""
    cleaned = (relative or "").replace("\\", "/").strip().strip("/")
    parts = PurePosixPath(cleaned).parts if cleaned else ()
    if any(part in ("..", "") or part.startswith("~") for part in parts):
        raise BatchError("That folder name isn't allowed.")
    root = inbox_root()
    path = (root.joinpath(*parts)).resolve()
    if path != root and root not in path.parents:
        raise BatchError("Folders must be inside the CLIPFORGE inbox folder.")
    return path


def _relative(path: Path) -> str:
    rel = path.resolve().relative_to(inbox_root())
    return "" if str(rel) == "." else rel.as_posix()


def video_files(folder: Path, *, recursive: bool, limit: int = 5000) -> list[tuple[Path, str]]:
    """(file, path inside the folder) for every video, skipping hidden, system and Processed folders."""
    found: list[tuple[Path, str]] = []
    stack = [folder]
    while stack and len(found) < limit:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith(".") or entry.is_symlink():
                continue  # never follow links out of the inbox
            if entry.is_dir():
                if recursive and entry.name.lower() not in SKIP_DIRS:
                    stack.append(entry)
            elif entry.suffix.lower() in VIDEO_EXT:
                found.append((entry, entry.relative_to(folder).as_posix()))
    return found


def browse(relative: str = "") -> dict[str, Any]:
    """Sub-folders of an inbox folder, for the folder picker."""
    settings = get_settings()
    root = inbox_root()
    path = resolve_folder(relative)
    exists = path.is_dir()
    folders = []
    if exists:
        for entry in sorted(path.iterdir(), key=lambda p: p.name.lower()):
            if entry.is_dir() and not entry.is_symlink() and not entry.name.startswith(".") \
                    and entry.name.lower() not in SKIP_DIRS:
                folders.append({"name": entry.name, "path": _relative(entry),
                                "videos": len(video_files(entry, recursive=True, limit=1000))})
    return {"root": settings.inbox_display or str(root), "path": _relative(path) if exists else relative,
            "exists": exists, "folders": folders,
            "videos": len(video_files(path, recursive=False, limit=1000)) if exists else 0,
            "writable": exists and _writable(path)}


def _writable(path: Path) -> bool:
    import os

    return os.access(path, os.W_OK)


# ------------------------------------------------------------------ batches
def create_batch(db: Session, *, name: str, recipe: Recipe, origin: str, user_id: uuid.UUID | None,
                 watch_folder_id: uuid.UUID | None = None) -> Batch:
    batch = Batch(name=name[:200] or "Batch", origin=origin, recipe=recipe.model_dump(mode="json"),
                  status="open" if origin == "upload" else "running", created_by=user_id, watch_folder_id=watch_folder_id)
    db.add(batch)
    db.flush()
    return batch


def add_upload(db: Session, batch: Batch, asset: MediaAsset, filename: str) -> BatchItem:
    """A file the browser uploaded for this batch: start its project right away."""
    if batch.origin != "upload" or batch.status in ("cancelled", "completed"):
        raise BatchError("This batch doesn't take new videos.")
    name = filename[:1000] or asset.info.get("filename") or "video"
    item = db.execute(select(BatchItem).where(BatchItem.batch_id == batch.id, BatchItem.asset_id == asset.id)).scalars().first()
    if item is not None:
        return item  # the same file added twice (a retried upload)
    item = BatchItem(batch_id=batch.id, filename=name, size_bytes=asset.size_bytes or 0, mtime=0, sha256=asset.sha256,
                     asset_id=asset.id, status="pending")
    db.add(item)
    db.flush()
    if batch.status != "paused":
        start_item(db, batch, item, asset)
    return item


def start_item(db: Session, batch: Batch, item: BatchItem, asset: MediaAsset) -> Project | None:
    """Create the item's project and start the normal video workflow."""
    if asset.sha256:
        twin = db.execute(select(BatchItem).where(BatchItem.batch_id == batch.id, BatchItem.sha256 == asset.sha256,
                                                  BatchItem.id != item.id, BatchItem.project_id.is_not(None))).scalars().first()
        if twin is not None:
            item.status, item.error = "duplicate", f"Same video as {twin.filename}"
            return None
    recipe = Recipe(**batch.recipe)
    source = db.execute(select(Source).where(Source.platform == "upload",
                                             Source.external_id == (asset.sha256 or str(asset.id)))).scalar_one_or_none()
    if source is None:
        source = Source(platform="upload", source_type="file", external_id=asset.sha256 or str(asset.id),
                        owner_user_id=batch.created_by, media_asset_id=asset.id, media_status="available",
                        access_status="FULL_MEDIA_ACCESS", content_sha256=asset.sha256,
                        source_metadata={"filename": PurePosixPath(item.filename).name})
        db.add(source)
        db.flush()
        asset.source_id = source.id
    title = PurePosixPath(item.filename).stem.replace("_", " ").replace("-", " ").strip()[:200] or "Video"
    project = Project(user_id=batch.created_by, source_id=source.id, title=title,
                      instruction=recipe.instruction or f"Batch “{batch.name}”: {recipe.summary()}",
                      request=recipe.to_request(db, batch_id=str(batch.id)), status="queued", status_label="Queued")
    db.add(project)
    db.flush()
    queue.enqueue(db, "workflow.video", {"project_id": str(project.id), "source_id": str(source.id)},
                  project_id=project.id, user_id=batch.created_by, dedupe_key=f"project:{project.id}",
                  status_label="Queued", max_attempts=4, priority=BATCH_PRIORITY)
    item.project_id, item.asset_id, item.sha256 = project.id, asset.id, asset.sha256
    item.status, item.error = "started", None
    if batch.status == "open" and batch.origin == "folder":
        batch.status = "running"
    return project


def close_batch(db: Session, batch: Batch) -> None:
    """The browser finished uploading: no more videos will be added."""
    if batch.status == "open":
        batch.status = "running"
    refresh_status(db, batch)


def pause_batch(db: Session, batch: Batch) -> int:
    paused = 0
    for job in _active_jobs(db, batch):
        if job.status in ("queued", "running", "waiting"):
            queue.pause(db, job.id)
            paused += 1
    batch.state = {**(batch.state or {}), "status_before_pause": batch.status}
    batch.status = "paused"
    return paused


def resume_batch(db: Session, batch: Batch) -> int:
    resumed = 0
    for job in _active_jobs(db, batch):
        queue.unpause(db, job.id)
        resumed += 1
    batch.status = (batch.state or {}).get("status_before_pause") or "running"
    for item in db.execute(select(BatchItem).where(BatchItem.batch_id == batch.id, BatchItem.status == "pending",
                                                   BatchItem.asset_id.is_not(None))).scalars():
        asset = db.get(MediaAsset, item.asset_id)
        if asset is not None:
            start_item(db, batch, item, asset)
    return resumed


def cancel_batch(db: Session, batch: Batch) -> int:
    cancelled = 0
    for job in _active_jobs(db, batch):
        queue.cancel(db, job.id, "Batch cancelled")
        cancelled += 1
    for item in db.execute(select(BatchItem).where(BatchItem.batch_id == batch.id,
                                                   BatchItem.status.in_(("pending", "copying")))).scalars():
        item.status = "cancelled"
    batch.status = "cancelled"
    if batch.watch_folder_id:
        folder = db.get(WatchFolder, batch.watch_folder_id)
        if folder is not None:
            folder.enabled = False
    return cancelled


def _active_jobs(db: Session, batch: Batch) -> list:
    from ..db.models import Job

    project_ids = [pid for (pid,) in db.execute(select(BatchItem.project_id).where(
        BatchItem.batch_id == batch.id, BatchItem.project_id.is_not(None))).all()]
    if not project_ids:
        return []
    jobs = db.execute(select(Job).where(Job.project_id.in_(project_ids), Job.parent_id.is_(None),
                                        Job.status.in_(queue.ACTIVE))).scalars().all()
    jobs += db.execute(select(Job).where(Job.type == "batch.ingest_file", Job.status.in_(queue.ACTIVE),
                                         Job.payload["batch_id"].astext == str(batch.id))).scalars().all()
    return jobs


def summary(db: Session, batch: Batch, *, items: bool = False) -> dict[str, Any]:
    rows = db.execute(select(BatchItem, Project).outerjoin(Project, Project.id == BatchItem.project_id)
                      .where(BatchItem.batch_id == batch.id).order_by(BatchItem.created_at)).all()
    counts = {"total": 0, "waiting": 0, "processing": 0, "ready": 0, "scheduled": 0, "published": 0, "attention": 0,
              "skipped": 0}
    listed = []
    for item, project in rows:
        counts["total"] += 1
        bucket = _bucket(item, project)
        counts[bucket] += 1
        if items:
            listed.append({"id": str(item.id), "filename": item.filename, "size_bytes": item.size_bytes,
                           "status": item.status, "error": item.error, "bucket": bucket,
                           "project_id": str(project.id) if project else None,
                           "project_status": project.status if project else None,
                           "project_label": project.status_label if project else None})
    recipe = Recipe(**batch.recipe)
    folder = db.get(WatchFolder, batch.watch_folder_id) if batch.watch_folder_id else None
    out = {"id": str(batch.id), "name": batch.name, "origin": batch.origin, "status": batch.status,
           "recipe": batch.recipe, "recipe_summary": recipe.summary(), "counts": counts,
           "next_slot": (batch.state or {}).get("next_slot"), "created_at": batch.created_at,
           "folder": _folder_out(folder) if folder else None}
    if items:
        out["items"] = listed
    return out


def _bucket(item: BatchItem, project: Project | None) -> str:
    if item.status in ("duplicate", "cancelled"):
        return "skipped"
    if item.status == "failed":
        return "attention"
    if project is None:
        return "waiting"
    status = project.status
    if status in ("published", "partially_published"):
        return "published"
    if status == "scheduled":
        return "scheduled"
    if status in ("ready", "completed", "awaiting_approval"):
        return "ready"
    if status in ("failed", "access_required", "unavailable"):
        return "attention"
    return "processing"


def refresh_status(db: Session, batch: Batch) -> None:
    """An upload batch whose videos have all finished is completed."""
    if batch.origin != "upload" or batch.status != "running":
        return
    counts = summary(db, batch)["counts"]
    if counts["total"] and counts["waiting"] == 0 and counts["processing"] == 0:
        batch.status = "completed"


# ---------------------------------------------------------------- posting slots
def _slot_times(batch: Batch) -> list[time]:
    recipe = Recipe(**batch.recipe)
    return [time(int(t[:2]), int(t[3:])) for t in recipe.schedule.times]


def next_slots(db: Session, batch: Batch, *, after: datetime) -> Any:
    """Posting slots of this batch (UTC), in order, from ``after`` on."""
    from ..publishing import schedule

    tz = schedule.zone(db)
    recipe = Recipe(**batch.recipe)
    times = _slot_times(batch)
    day = max(recipe.schedule.start_date or after.astimezone(tz).date(), after.astimezone(tz).date())
    for _ in range(800):
        for slot_time in times:
            slot = datetime.combine(day, slot_time, tz).astimezone(UTC)
            if slot >= after:
                yield slot
        day += timedelta(days=1)


def allocate_slots(db: Session, batch_id: uuid.UUID, entries: list[tuple[OutputPackage, int]]) -> tuple[dict, list[str]]:
    """Give each clip (all its platforms together) the batch's next free posting slot.

    Projects of a batch finish at different times; the batch row is locked so two of
    them never take the same slot. A slot is skipped when a platform's daily limit is
    already reached that day.
    """
    from ..publishing import schedule

    batch = db.execute(select(Batch).where(Batch.id == batch_id).with_for_update()).scalar_one()
    now = utcnow()
    floor = now + timedelta(minutes=2)
    stored = (batch.state or {}).get("next_slot")
    cursor = max(floor, datetime.fromisoformat(stored)) if stored else floor
    slots = next_slots(db, batch, after=cursor)
    by_rank: dict[int, list[OutputPackage]] = {}
    for package, rank in sorted(entries, key=lambda e: (e[1], e[0].platform)):
        by_rank.setdefault(rank, []).append(package)
    times: dict[str, datetime] = {}
    problems: list[str] = []
    last: datetime | None = None
    for rank, packages in by_rank.items():
        for _attempt in range(400):
            slot = next(slots, None)
            if slot is None:
                break
            try:
                with db.begin_nested():
                    for package in packages:
                        times[str(package.id)] = schedule.apply(db, package, slot)
                last = slot
                break
            except schedule.ScheduleError as exc:
                if exc.code != "daily_limit":
                    problems.append(f"Clip {rank}: {exc}")
                    break
                for package in packages:
                    times.pop(str(package.id), None)
        else:
            problems.append(f"Clip {rank}: no free posting slot within the next year.")
    if last is not None:
        batch.state = {**(batch.state or {}), "next_slot": (last + timedelta(seconds=1)).isoformat()}
    return times, problems


# -------------------------------------------------------------- watched folders
def create_watch_folder(db: Session, *, path: str, recipe: Recipe, name: str | None, user_id: uuid.UUID | None,
                        include_subfolders: bool = True, after_processing: str = "keep",
                        include_existing: bool = True) -> WatchFolder:
    folder_path = resolve_folder(path)
    if not folder_path.is_dir():
        raise BatchError("That folder doesn't exist in the CLIPFORGE inbox.")
    relative = _relative(folder_path)
    if db.execute(select(WatchFolder).where(WatchFolder.path == relative)).scalar_one_or_none() is not None:
        raise BatchError("That folder is already being watched — change its settings instead.")
    if after_processing == "move" and not _writable(folder_path):
        raise BatchError("CLIPFORGE can't write to that folder, so it can't move processed videos. Keep them in place instead.")
    label = name or (folder_path.name if relative else "Inbox")
    folder = WatchFolder(name=label[:200], path=relative, recipe=recipe.model_dump(mode="json"), enabled=True,
                         include_subfolders=include_subfolders, after_processing=after_processing, created_by=user_id,
                         state={})
    db.add(folder)
    db.flush()
    batch = create_batch(db, name=f"Folder: {label}", recipe=recipe, origin="folder", user_id=user_id,
                         watch_folder_id=folder.id)
    folder.batch_id = batch.id
    if not include_existing:
        folder.state = {"baseline": [[rel, f.stat().st_size, int(f.stat().st_mtime)]
                                     for f, rel in video_files(folder_path, recursive=include_subfolders)]}
    return folder


def _folder_out(folder: WatchFolder) -> dict[str, Any]:
    return {"id": str(folder.id), "name": folder.name, "path": folder.path, "enabled": folder.enabled,
            "include_subfolders": folder.include_subfolders, "after_processing": folder.after_processing,
            "last_scan_at": folder.last_scan_at, "last_error": folder.last_error,
            "waiting_files": len((folder.state or {}).get("pending", {})),
            "batch_id": str(folder.batch_id) if folder.batch_id else None}


def scan_folder(db: Session, folder: WatchFolder, *, now: datetime | None = None) -> int:
    """Find new, completely written videos and queue them; returns how many were queued."""
    import time as clock

    now = now or utcnow()
    batch = db.get(Batch, folder.batch_id) if folder.batch_id else None
    if not folder.enabled or batch is None or batch.status in ("paused", "cancelled"):
        return 0
    try:
        path = resolve_folder(folder.path)
    except BatchError as exc:
        folder.last_error = str(exc)
        return 0
    if not path.is_dir():
        folder.last_error = "The folder can't be found (renamed, deleted, or the drive isn't connected)."
        folder.last_scan_at = now
        return 0
    known = {(f, s, m) for f, s, m in db.execute(select(BatchItem.filename, BatchItem.size_bytes, BatchItem.mtime)
                                                 .where(BatchItem.batch_id == batch.id)).all()}
    state = dict(folder.state or {})
    known |= {tuple(entry) for entry in state.get("baseline", [])}
    pending: dict[str, list[int]] = state.get("pending", {})
    still_pending: dict[str, list[int]] = {}
    queued = 0
    wall = clock.time()
    for file, rel in video_files(path, recursive=folder.include_subfolders):
        try:
            info = file.stat()
        except OSError:
            continue
        signature = [info.st_size, int(info.st_mtime)]
        if (rel, signature[0], signature[1]) in known or info.st_size == 0:
            continue
        stable = pending.get(rel) == signature or wall - info.st_mtime >= STABLE_AFTER_S
        if not stable or queued >= MAX_NEW_PER_SCAN:
            still_pending[rel] = signature  # being copied (or too many at once): look again next time
            continue
        item = BatchItem(batch_id=batch.id, filename=rel, size_bytes=signature[0], mtime=signature[1], status="pending")
        db.add(item)
        db.flush()
        queue.enqueue(db, "batch.ingest_file", {"item_id": str(item.id), "batch_id": str(batch.id)}, queue="media",
                      dedupe_key=f"ingest:{item.id}", priority=BATCH_PRIORITY, status_label="Copying video")
        queued += 1
    state["pending"] = still_pending
    folder.state = state
    folder.last_scan_at = now
    folder.last_error = None
    return queued


def scan_all(db: Session) -> dict[str, int]:
    folders = db.execute(select(WatchFolder).where(WatchFolder.enabled.is_(True))).scalars().all()
    return {folder.name: scan_folder(db, folder) for folder in folders}


def ingest_file(db: Session, item_id: uuid.UUID, *, cancelled=None) -> dict[str, Any]:
    """Copy one watched-folder video into CLIPFORGE's storage and start its project (media worker)."""
    from ..storage import get_storage, looks_like_media

    item = db.get(BatchItem, item_id)
    batch = db.get(Batch, item.batch_id) if item else None
    folder = db.get(WatchFolder, batch.watch_folder_id) if batch and batch.watch_folder_id else None
    if item is None or batch is None or folder is None:
        return {"skipped": "gone"}
    if batch.status == "cancelled":
        item.status = "cancelled"
        return {"skipped": "cancelled"}
    base = resolve_folder(folder.path)
    file = (base / item.filename).resolve()
    if base not in file.parents or file.is_symlink():
        item.status, item.error = "failed", "The file is outside the watched folder."
        return {"failed": item.error}
    if not file.is_file():
        item.status, item.error = "failed", "The file was moved or deleted before it could be copied."
        return {"failed": item.error}
    info = file.stat()
    if info.st_size != item.size_bytes or int(info.st_mtime) != item.mtime:
        db.delete(item)  # it changed after it was seen: the next scan picks up the new version
        return {"skipped": "changed"}
    settings = get_settings()
    if info.st_size > settings.max_upload_mb * 1024 * 1024:
        item.status, item.error = "failed", f"Larger than the {settings.max_upload_mb} MB limit."
        return {"failed": item.error}
    item.status = "copying"
    db.flush()
    tmp_dir = settings.work_dir / "ingest" / str(item.id)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"incoming{file.suffix.lower()}"
    digest = hashlib.sha256()
    try:
        with open(file, "rb") as src, open(tmp, "wb") as dst:
            while chunk := src.read(1 << 20):
                if cancelled and cancelled():
                    raise BatchError("Cancelled")
                digest.update(chunk)
                dst.write(chunk)
        if not looks_like_media(tmp):
            item.status, item.error = "failed", "This file doesn't look like a video."
            return {"failed": item.error}
        sha = digest.hexdigest()
        asset = db.execute(select(MediaAsset).where(MediaAsset.sha256 == sha, MediaAsset.kind == "upload")).scalars().first()
        if asset is None:
            key = f"uploads/{sha[:2]}/{sha}{file.suffix.lower()}"
            stored = get_storage().put_file(tmp, key, move=True)
            asset = MediaAsset(kind="upload", storage_key=key, mime="video/mp4" if file.suffix.lower() == ".mp4" else
                               "application/octet-stream", size_bytes=stored, sha256=sha,
                               info={"filename": PurePosixPath(item.filename).name, "validated": False,
                                     "origin": {"watch_folder": folder.name}})
            db.add(asset)
            db.flush()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    item.sha256 = sha
    project = start_item(db, batch, item, asset)
    if folder.after_processing == "move" and item.status in ("started", "duplicate"):
        target = base / "Processed" / item.filename
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(file), str(target))
        except OSError as exc:
            log.warning("Could not move %s to Processed: %s", file, exc)
    return {"item": str(item.id), "status": item.status, "project_id": str(project.id) if project else None}


def stats_for_projects(db: Session, project_ids: list[uuid.UUID]) -> dict[str, str]:
    """batch id per project (for project lists)."""
    rows = db.execute(select(BatchItem.project_id, BatchItem.batch_id).where(BatchItem.project_id.in_(project_ids))).all()
    return {str(p): str(b) for p, b in rows}


def count_open(db: Session) -> int:
    return db.execute(select(func.count()).select_from(Batch).where(Batch.status.in_(("open", "running")))).scalar_one()


def clip_rank(db: Session, package: OutputPackage) -> int:
    clip = db.get(Clip, package.clip_id)
    return clip.rank if clip else 0
