"""My Videos: projects, clips, renders, packages and publications."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...db.base import get_db
from ...db.models import (
    Clip,
    Job,
    MediaAsset,
    MetricSnapshot,
    OutputPackage,
    Project,
    Publication,
    Render,
    Source,
)
from ...jobs import queue
from ...publishing import schedule
from ...security.audit import audit
from ...storage import get_storage
from ..deps import Principal, client_ip, editor, viewer

log = logging.getLogger(__name__)

router = APIRouter(tags=["projects"])


class ClipEdit(BaseModel):
    start_s: float | None = Field(None, ge=0)
    end_s: float | None = Field(None, gt=0)
    title: str | None = Field(None, max_length=300)
    caption_style: str | None = Field(None, pattern="^(bold_pop|karaoke_fill|clean_lower|boxed)$")
    burn_captions: bool | None = None
    rerender: bool = True


class PackageEdit(BaseModel):
    title: str | None = Field(None, max_length=100)
    description: str | None = Field(None, max_length=5000)
    tags: list[str] | None = Field(None, max_length=30)
    caption: str | None = Field(None, max_length=2200)
    hashtags: list[str] | None = Field(None, max_length=30)
    privacy: str | None = Field(None, pattern="^(private|unlisted|public)$")


class PublishRequest(BaseModel):
    #: When the post should go live (ISO 8601 with a time zone). Leave out to publish now.
    scheduled_at: datetime | None = None


def _asset_url(asset_id) -> str | None:
    return f"/api/media/{asset_id}" if asset_id else None


def _project_summary(db: Session, p: Project) -> dict:
    source = db.get(Source, p.source_id) if p.source_id else None
    clips = db.execute(select(func.count()).select_from(Clip).where(Clip.project_id == p.id)).scalar_one()
    cover = db.execute(select(Render.thumbnail_asset_id).join(Clip, Clip.id == Render.clip_id)
                       .where(Clip.project_id == p.id, Render.thumbnail_asset_id.isnot(None)).order_by(Clip.rank)).scalars().first()
    job = db.execute(select(Job).where(Job.project_id == p.id, Job.parent_id.is_(None), Job.type == "workflow.video")
                     .order_by(Job.created_at.desc())).scalars().first()
    return {
        "id": str(p.id), "title": p.title, "status": p.status, "status_label": p.status_label,
        "analysis_level": p.analysis_level, "created_at": p.created_at, "updated_at": p.updated_at,
        "clips": clips, "thumbnail_url": _asset_url(cover),
        "source": {"platform": source.platform, "url": source.normalized_url, "access_status": source.access_status,
                   "title": (source.source_metadata or {}).get("title")} if source else None,
        "job": queue.job_summary(job) if job else None,
    }


@router.get("/projects")
def list_projects(limit: int = 50, db: Session = Depends(get_db), _: Principal = Depends(viewer)) -> list[dict]:
    rows = db.execute(select(Project).order_by(Project.updated_at.desc()).limit(min(limit, 200))).scalars()
    return [_project_summary(db, p) for p in rows]


@router.get("/projects/{project_id}")
def project_detail(project_id: uuid.UUID, db: Session = Depends(get_db), _: Principal = Depends(viewer)) -> dict:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, {"code": "not_found", "message": "Project not found."})
    source = db.get(Source, project.source_id) if project.source_id else None
    clips_out = []
    for clip in db.execute(select(Clip).where(Clip.project_id == project.id).order_by(Clip.rank)).scalars():
        renders = []
        for r in db.execute(select(Render).where(Render.clip_id == clip.id).order_by(Render.created_at)).scalars():
            live = r.status != "expired"  # expired renders' files were removed by storage clean-up
            renders.append({"id": str(r.id), "aspect": r.aspect, "status": r.status,
                            "video_url": _asset_url(r.video_asset_id) if live else None,
                            "srt_url": _asset_url(r.srt_asset_id) if live else None,
                            "vtt_url": _asset_url(r.vtt_asset_id) if live else None,
                            "thumbnail_url": _asset_url(r.thumbnail_asset_id) if live else None,
                            "captions_burned": r.captions_burned,
                            "quality": {"passed": r.quality_report.get("passed"),
                                        "checks": r.quality_report.get("checks", []),
                                        "loudness": r.quality_report.get("loudness")},
                            "subtitles": {k: r.subtitle_report.get(k) for k in ("passed", "issues", "review_words", "max_cps",
                                                                                 "misspelled", "language")},
                            "edits": {k: (r.edit_decisions or {}).get(k) for k in ("removed_silence_s", "crop_strategies",
                                                                                   "punch_in_cuts", "notes", "kept_intervals")}})
        packages = []
        for pkg in db.execute(select(OutputPackage).where(OutputPackage.clip_id == clip.id)).scalars():
            pubs = []
            for pub in db.execute(select(Publication).where(Publication.package_id == pkg.id)).scalars():
                latest = db.execute(select(MetricSnapshot).where(MetricSnapshot.publication_id == pub.id)
                                    .order_by(MetricSnapshot.collected_at.desc())).scalars().first()
                pubs.append({"id": str(pub.id), "status": pub.status, "url": pub.external_url, "external_id": pub.external_id,
                             "published_at": pub.published_at, "scheduled_at": pub.scheduled_at, "last_error": pub.last_error,
                             "metrics": latest.metrics if latest else None,
                             "unavailable_metrics": latest.unavailable if latest else None})
            packages.append({"id": str(pkg.id), "platform": pkg.platform, "status": pkg.status, "title": pkg.title,
                             "description": pkg.description, "tags": pkg.tags, "caption": pkg.caption,
                             "hashtags": pkg.hashtags, "privacy": pkg.privacy, "metadata_method": pkg.metadata_method,
                             "thumbnail_url": _asset_url(pkg.thumbnail_asset_id or pkg.cover_asset_id),
                             "scheduled_at": pkg.scheduled_at, "schedule_mode": schedule.mode(pkg),
                             "publications": pubs})
        clips_out.append({"id": str(clip.id), "rank": clip.rank, "start_s": clip.start_s, "end_s": clip.end_s,
                          "title": clip.title, "hook": clip.hook, "transcript": clip.transcript_text, "scores": clip.scores,
                          "total_score": clip.total_score, "reasons": clip.reasons, "status": clip.status,
                          "scoring_method": clip.scoring_method, "renders": renders, "packages": packages})
    jobs = db.execute(select(Job).where(Job.project_id == project.id, Job.parent_id.is_(None)).order_by(Job.created_at)).scalars()
    return {**_project_summary(db, project), "instruction": project.instruction, "request": project.request,
            "limitations": project.limitations, "errors": project.errors,
            "access": (source.access_detail if source else None), "clips": clips_out,
            "jobs": [queue.job_summary(j) for j in jobs]}


@router.patch("/clips/{clip_id}")
def edit_clip(clip_id: uuid.UUID, body: ClipEdit, request: Request, db: Session = Depends(get_db),
              principal: Principal = Depends(editor)) -> dict:
    clip = db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(404, {"code": "not_found", "message": "Clip not found."})
    if body.start_s is not None:
        clip.start_s = body.start_s
    if body.end_s is not None:
        clip.end_s = body.end_s
    if clip.end_s - clip.start_s < 2:
        raise HTTPException(400, {"code": "range", "message": "A clip must be at least 2 seconds long."})
    if body.title is not None:
        clip.title = body.title
    job = None
    if body.rerender and (body.start_s is not None or body.end_s is not None or body.caption_style or body.burn_captions is not None):
        for render in db.execute(select(Render).where(Render.clip_id == clip.id)).scalars():
            render.status = "superseded"
        options = {k: v for k, v in {"caption_style": body.caption_style, "burn_captions": body.burn_captions}.items() if v is not None}
        job = queue.enqueue(db, "media.render", {"clip_id": str(clip.id), "options": options}, queue="media",
                            project_id=clip.project_id, user_id=principal.id, status_label="Rendering")
        clip.status = "selected"
    audit(db, "clip.edit", user_id=principal.id, target_type="clip", target_id=clip_id,
          details=body.model_dump(exclude_none=True), ip=client_ip(request))
    return {"ok": True, "render_job_id": str(job.id) if job else None}


@router.patch("/packages/{package_id}")
def edit_package(package_id: uuid.UUID, body: PackageEdit, request: Request, db: Session = Depends(get_db),
                 principal: Principal = Depends(editor)) -> dict:
    package = db.get(OutputPackage, package_id)
    if package is None:
        raise HTTPException(404, {"code": "not_found", "message": "Package not found."})
    privacy_before = package.privacy
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(package, key, value)
    package.metadata_method = f"{package.metadata_method.split('+edited')[0]}+edited"
    if package.status == "scheduled":
        # A waiting video picks up the edits before it goes live; a privacy change can switch the mechanism.
        if schedule.remote_publication(db, package.id) is not None:
            schedule.kick(db)
        elif package.privacy != privacy_before:
            schedule.ensure_job(db, package, user_id=principal.id)
    audit(db, "package.edit", user_id=principal.id, target_type="package", target_id=package_id, ip=client_ip(request))
    return {"ok": True}


def _package(db: Session, package_id: uuid.UUID) -> OutputPackage:
    package = db.get(OutputPackage, package_id)
    if package is None:
        raise HTTPException(404, {"code": "not_found", "message": "Package not found."})
    return package


def _schedule_error(exc: schedule.ScheduleError) -> HTTPException:
    return HTTPException(409 if exc.code in ("daily_limit", "published", "not_scheduled") else 400,
                         {"code": exc.code, "message": str(exc)})


@router.post("/packages/{package_id}/publish")
def publish_package(package_id: uuid.UUID, request: Request, body: PublishRequest | None = None,
                    db: Session = Depends(get_db), principal: Principal = Depends(editor)) -> dict:
    """Publish now, or schedule the exact time the post goes live (``scheduled_at``)."""
    package = _package(db, package_id)
    if package.status == "published":
        raise HTTPException(409, {"code": "published", "message": "This clip is already published."})
    if body is not None and body.scheduled_at is not None:
        try:
            result = schedule.schedule(db, package, body.scheduled_at, user_id=principal.id)
        except schedule.ScheduleError as exc:
            raise _schedule_error(exc) from exc
        audit(db, "publish.schedule", user_id=principal.id, target_type="package", target_id=package_id,
              details={"platform": package.platform, "scheduled_at": result["scheduled_at"], "mode": result["mode"]},
              ip=client_ip(request))
        return result
    package.scheduled_at = None
    package.status = "approved"
    db.flush()
    job = None
    if schedule.remote_publication(db, package.id) is not None:
        schedule.kick(db)  # the video already waits on YouTube: make it live now
    else:
        job = schedule.ensure_job(db, package, user_id=principal.id)
    schedule.refresh_project_status(db, db.get(Clip, package.clip_id).project_id)
    audit(db, "publish.request", user_id=principal.id, target_type="package", target_id=package_id,
          details={"platform": package.platform}, ip=client_ip(request))
    return {"job_id": str(job.id) if job else None, "scheduled_at": None, "mode": "now"}


@router.post("/packages/{package_id}/unschedule")
def unschedule_package(package_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                       principal: Principal = Depends(editor)) -> dict:
    """Cancel a scheduled post. A video already uploaded to YouTube stays there, private."""
    package = _package(db, package_id)
    try:
        result = schedule.unschedule(db, package)
    except schedule.ScheduleError as exc:
        raise _schedule_error(exc) from exc
    audit(db, "publish.unschedule", user_id=principal.id, target_type="package", target_id=package_id,
          details={"platform": package.platform}, ip=client_ip(request))
    return result


@router.get("/schedule")
def upcoming_posts(db: Session = Depends(get_db), _: Principal = Depends(viewer)) -> dict:
    """Scheduled and missed posts, soonest first, with how each one will go out."""
    rows = db.execute(select(OutputPackage, Clip, Project)
                      .join(Clip, Clip.id == OutputPackage.clip_id).join(Project, Project.id == Clip.project_id)
                      .where(OutputPackage.status.in_(("scheduled", "missed")))).all()
    items = []
    for package, clip, project in rows:
        remote = schedule.remote_publication(db, package.id)
        job = next(iter(schedule.active_jobs(db, package.id)), None) if remote is None else None
        at = package.scheduled_at or (job.run_after if job else None)
        items.append({"package_id": str(package.id), "platform": package.platform, "status": package.status,
                      "title": package.title or clip.title or "Untitled", "clip_rank": clip.rank,
                      "project_id": str(project.id), "project_title": project.title,
                      "scheduled_at": at, "owner_time": package.scheduled_at is not None,
                      "mode": schedule.mode(package), "privacy": package.privacy,
                      "uploaded": remote is not None, "remote_status": remote.status if remote else None,
                      "on_hold": not schedule.publishing_allowed(db, package.platform),
                      "last_error": remote.last_error if remote else None,
                      "thumbnail_url": _asset_url(package.thumbnail_asset_id or package.cover_asset_id)})
    items.sort(key=lambda i: (i["status"] != "missed", i["scheduled_at"].timestamp() if i["scheduled_at"] else float("inf")))
    return {"timezone": schedule.zone(db).key, "items": items}


@router.delete("/projects/{project_id}")
def delete_project(project_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                   principal: Principal = Depends(editor)) -> dict:
    """Delete a project and its rendered files (source media of shared sources is kept)."""
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, {"code": "not_found", "message": "Project not found."})
    for job in db.execute(select(Job).where(Job.project_id == project_id, Job.status.in_(queue.ACTIVE))).scalars():
        queue.cancel(db, job.id, "Project deleted")
    storage = get_storage()
    for asset in db.execute(select(MediaAsset).where(MediaAsset.project_id == project_id)).scalars():
        try:
            storage.delete(asset.storage_key)
        except OSError as exc:  # leftover files are removed by the retention clean-up
            log.warning("Could not delete %s: %s", asset.storage_key, exc)
    db.delete(project)
    audit(db, "project.delete", user_id=principal.id, target_type="project", target_id=project_id, ip=client_ip(request))
    return {"ok": True}
