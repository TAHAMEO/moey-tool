"""Housekeeping: retention cleanup, token refresh and backups."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from datetime import timedelta

from sqlalchemy import delete, select

from ..config import get_settings
from ..db.base import session_scope, utcnow
from ..db.models import (
    Job,
    LLMCache,
    MediaAsset,
    OAuthState,
    OutputPackage,
    Publication,
    Render,
    SocialAccount,
    UserSession,
)
from ..jobs.registry import JobContext, handler
from ..platforms import google, instagram
from ..platforms.http import PlatformError
from ..services import app_settings
from ..storage import get_storage

log = logging.getLogger(__name__)


@handler("maintenance.cleanup", queue="default")
def cleanup(ctx: JobContext) -> dict:
    settings = get_settings()
    removed = {"work_dirs": 0, "source_files": 0, "renders": 0, "sessions": 0, "oauth_states": 0, "cache": 0}
    with session_scope() as db:
        retention = app_settings.get_section(db, "retention")
        cutoff = time.time() - retention.work_files_days * 86400
        for child in settings.work_dir.glob("*/*"):
            try:
                if child.stat().st_mtime < cutoff:
                    shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink(missing_ok=True)
                    removed["work_dirs"] += 1
            except FileNotFoundError:
                continue
        # Original downloads/uploads expire; transcripts, analyses and rendered clips are kept.
        source_cutoff = utcnow() - timedelta(days=retention.source_media_days)
        for asset in db.execute(select(MediaAsset).where(MediaAsset.kind.in_(("source", "upload", "mezzanine", "proxy")),
                                                         MediaAsset.created_at < source_cutoff)).scalars():
            if (asset.info or {}).get("deleted"):
                continue
            try:
                get_storage().delete(asset.storage_key)
            except Exception as exc:  # noqa: BLE001 - pragma: no cover - logged, retried next run
                log.warning("Could not delete %s: %s", asset.storage_key, exc)
                continue
            asset.info = {**(asset.info or {}), "deleted": True, "deleted_at": utcnow().isoformat()}
            removed["source_files"] += 1
        # Finished clips expire too (they can be re-rendered from the kept analysis), except while
        # one of their publications is still in progress.
        render_cutoff = utcnow() - timedelta(days=retention.keep_renders_days)
        busy = set(db.execute(select(OutputPackage.clip_id).join(Publication, Publication.package_id == OutputPackage.id)
                              .where(Publication.status.notin_(("published", "failed", "cancelled")))).scalars())
        # Clips waiting for their scheduled time keep their files too.
        busy |= set(db.execute(select(OutputPackage.clip_id).where(OutputPackage.status.in_(("scheduled", "approved"))))
                    .scalars())
        for render in db.execute(select(Render).where(Render.status == "done", Render.created_at < render_cutoff)).scalars():
            if render.clip_id in busy:
                continue
            for asset_id in (render.video_asset_id, render.srt_asset_id, render.vtt_asset_id, render.thumbnail_asset_id):
                asset = db.get(MediaAsset, asset_id) if asset_id else None
                if asset is None or (asset.info or {}).get("deleted"):
                    continue
                try:
                    get_storage().delete(asset.storage_key)
                except OSError as exc:  # retried on the next run
                    log.warning("Could not delete %s: %s", asset.storage_key, exc)
                    continue
                asset.info = {**(asset.info or {}), "deleted": True, "deleted_at": utcnow().isoformat()}
            render.status = "expired"
            removed["renders"] += 1
        removed["sessions"] = db.execute(delete(UserSession).where(UserSession.expires_at < utcnow())).rowcount
        removed["oauth_states"] = db.execute(delete(OAuthState).where(OAuthState.created_at < utcnow() - timedelta(hours=1))).rowcount
        removed["cache"] = db.execute(delete(LLMCache).where(LLMCache.expires_at < utcnow())).rowcount
        # Routine background checks (folder scans, schedule checks, …) leave a finished job every few
        # minutes; keep a week of them. Jobs that belong to a project are kept with the project.
        removed["routine_jobs"] = db.execute(delete(Job).where(
            Job.project_id.is_(None), Job.parent_id.is_(None), Job.status.in_(("completed", "cancelled")),
            Job.finished_at < utcnow() - timedelta(days=7),
            Job.type.in_(("folders.scan", "maintenance.publish_scheduled", "maintenance.cleanup",
                          "maintenance.refresh_tokens", "analytics.collect_due", "learning.run")))).rowcount
    return removed


@handler("maintenance.refresh_tokens", queue="default", needs_secrets=True)
def refresh_tokens(ctx: JobContext) -> dict:
    refreshed, failed = 0, 0
    with session_scope() as db:
        for account in db.execute(select(SocialAccount).where(SocialAccount.status == "connected")).scalars():
            try:
                if account.platform in ("youtube", "google_drive"):
                    google.access_token(db, account)
                elif account.platform == "instagram":
                    instagram.access_token(db, account)
                refreshed += 1
            except PlatformError as exc:
                failed += 1
                log.warning("Token refresh failed for %s: %s", account.platform, exc)
    return {"checked": refreshed, "failed": failed}


@handler("maintenance.publish_scheduled", queue="default", needs_secrets=True)
def publish_scheduled(ctx: JobContext) -> dict:
    """Every 5 minutes (and right after any schedule change): make scheduled posts match their plan."""
    from ..publishing import schedule

    return schedule.reconcile()


def run_backup(label: str = "scheduled") -> dict:
    """pg_dump (custom format) + memory export, keeping the newest 14 backups."""
    settings = get_settings()
    now = utcnow()
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    target = settings.backups_dir / f"{stamp}-{label}"
    target.mkdir(parents=True, exist_ok=True)
    dump = target / "database.dump"
    proc = subprocess.run(["pg_dump", "--format=custom", "--no-owner", "--file", str(dump), settings.psycopg_dsn],
                          check=False, capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        raise RuntimeError(f"pg_dump failed: {proc.stderr[-500:]}")
    manifest = {"name": target.name, "created_at": now.isoformat(), "database": dump.name,
                "size": dump.stat().st_size, "label": label}
    try:
        from ..memory.service import get_memory

        (target / "memory-export.json").write_text(json.dumps(get_memory().export(), default=str))
        manifest["memory_export"] = "memory-export.json"
    except Exception as exc:  # noqa: BLE001 - the database dump already contains the vectors
        manifest["memory_export_error"] = str(exc)[:200]
    history = settings.data_dir / "mem0" / "history.db"
    if history.exists():
        shutil.copy2(history, target / "mem0-history.db")
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2))
    backups = sorted(p for p in settings.backups_dir.iterdir() if p.is_dir())
    for old in backups[:-14]:
        shutil.rmtree(old, ignore_errors=True)
    return manifest


@handler("maintenance.backup", queue="default")
def backup(ctx: JobContext) -> dict:
    return run_backup()
