"""My Videos: projects, clips, renders, packages and publications."""

from __future__ import annotations

import logging
import uuid

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
            renders.append({"id": str(r.id), "aspect": r.aspect, "status": r.status, "video_url": _asset_url(r.video_asset_id),
                            "srt_url": _asset_url(r.srt_asset_id), "vtt_url": _asset_url(r.vtt_asset_id),
                            "thumbnail_url": _asset_url(r.thumbnail_asset_id), "captions_burned": r.captions_burned,
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
                             "published_at": pub.published_at, "last_error": pub.last_error,
                             "metrics": latest.metrics if latest else None,
                             "unavailable_metrics": latest.unavailable if latest else None})
            packages.append({"id": str(pkg.id), "platform": pkg.platform, "status": pkg.status, "title": pkg.title,
                             "description": pkg.description, "tags": pkg.tags, "caption": pkg.caption,
                             "hashtags": pkg.hashtags, "privacy": pkg.privacy, "metadata_method": pkg.metadata_method,
                             "thumbnail_url": _asset_url(pkg.thumbnail_asset_id or pkg.cover_asset_id),
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
    for key, value in body.model_dump(exclude_none=True).items():
        setattr(package, key, value)
    package.metadata_method = f"{package.metadata_method.split('+edited')[0]}+edited"
    audit(db, "package.edit", user_id=principal.id, target_type="package", target_id=package_id, ip=client_ip(request))
    return {"ok": True}


@router.post("/packages/{package_id}/publish")
def publish_package(package_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                    principal: Principal = Depends(editor)) -> dict:
    package = db.get(OutputPackage, package_id)
    if package is None:
        raise HTTPException(404, {"code": "not_found", "message": "Package not found."})
    clip = db.get(Clip, package.clip_id)
    job = queue.enqueue(db, "workflow.publish", {"package_id": str(package_id), "approved_by": str(principal.id)},
                        dedupe_key=f"publish:{package_id}", project_id=clip.project_id, user_id=principal.id,
                        status_label="Preparing upload")
    audit(db, "publish.request", user_id=principal.id, target_type="package", target_id=package_id,
          details={"platform": package.platform}, ip=client_ip(request))
    return {"job_id": str(job.id)}


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
