"""Changing a clip (its bounds, captions, title) and re-rendering it.

Shared by the clip editor in My Videos and by Brain Chat, so a change asked for in the chat does
exactly what the buttons do.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Clip, Job, MediaAsset, OutputPackage, Render
from ..jobs import queue
from ..storage import asset_present

CAPTION_STYLES = ("bold_pop", "karaoke_fill", "clean_lower", "boxed", "kinetic")


class EditError(ValueError):
    def __init__(self, message: str, *, code: str, status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


def source_duration(db: Session, clip: Clip) -> float | None:
    mez = db.execute(select(MediaAsset).where(MediaAsset.source_id == clip.source_id, MediaAsset.kind == "mezzanine")
                     .order_by(MediaAsset.created_at.desc())).scalars().first()
    return mez.duration_s if mez is not None else None


def edit_clip(db: Session, clip: Clip, *, start_s: float | None = None, end_s: float | None = None,
              title: str | None = None, caption_style: str | None = None, burn_captions: bool | None = None,
              rerender: bool = True, force_render: bool = False, user_id: uuid.UUID | None = None) -> Job | None:
    """Apply the changes; returns the re-render job when the video has to be made again."""
    if caption_style is not None and caption_style not in CAPTION_STYLES:
        raise EditError(f"Unknown caption style {caption_style!r}.", code="caption_style")
    if start_s is not None:
        clip.start_s = max(0.0, float(start_s))
    if end_s is not None:
        limit = source_duration(db, clip)
        clip.end_s = min(float(end_s), limit) if limit else float(end_s)
    if clip.end_s - clip.start_s < 2:
        raise EditError("A clip must be at least 2 seconds long.", code="range")
    if title is not None:
        clip.title = title
    changed_video = start_s is not None or end_s is not None or caption_style or burn_captions is not None
    if not (rerender and (changed_video or force_render)):
        return None
    prepared = db.execute(select(MediaAsset).where(MediaAsset.source_id == clip.source_id,
                                                   MediaAsset.kind == "mezzanine")).scalars().all()
    if not any(asset_present(a) for a in prepared):
        raise EditError("This clip's source video is no longer stored (storage clean-up removes source videos after "
                        "the number of days set in Settings → Data), so it can't be re-rendered. Process the video "
                        "again.", code="source_removed", status=409)
    for render in db.execute(select(Render).where(Render.clip_id == clip.id)).scalars():
        render.status = "superseded"
    options = {k: v for k, v in {"caption_style": caption_style, "burn_captions": burn_captions}.items() if v is not None}
    clip.status = "selected"
    return queue.enqueue(db, "media.render", {"clip_id": str(clip.id), "options": options}, queue="media",
                         project_id=clip.project_id, user_id=user_id, status_label="Rendering")


def set_title(db: Session, clip: Clip, title: str, *, platform: str | None = None) -> list[str]:
    """New title for the clip and its not-yet-published packages. Returns the platforms changed."""
    title = " ".join(title.split())[:100]
    if not title:
        raise EditError("The title is empty.", code="title")
    clip.title = title
    changed = []
    for package in db.execute(select(OutputPackage).where(OutputPackage.clip_id == clip.id)).scalars():
        if platform and platform != "all" and package.platform != platform:
            continue
        if package.status in ("published", "publishing"):
            continue
        if package.platform == "youtube":
            suffix = " #shorts" if package.title.endswith("#shorts") and len(title) <= 92 else ""
            package.title = title + suffix
        else:
            _first, _, rest = (package.caption or "").partition("\n")
            package.caption = title + ("\n" + rest if rest else "")
            package.title = title
        package.metadata_method = f"{package.metadata_method.split('+edited')[0]}+edited"
        changed.append(package.platform)
    return changed
