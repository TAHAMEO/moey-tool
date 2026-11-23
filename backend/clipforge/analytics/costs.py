"""Cost per clip and per published Short (P3), from what was actually metered.

* **AI calls**: every model call goes through the Model Hub, which records tokens and cost (USD)
  per job and project (``AIUsage``). Calls made for one clip's own job count for that clip; calls
  made for the whole video (choosing moments, writing copy) are shared equally by its clips.
  Local models cost nothing; a price the provider doesn't publish is marked *estimated*.
* **Processing time**: how long the jobs ran on this computer (seconds), split the same way.
* **Per published Short**: a clip's cost divided by the posts made from it; with views, the cost
  per 1,000 views. Nothing is estimated beyond what the Model Hub metered.
"""

from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import AIUsage, Clip, Job, MetricSnapshot, OutputPackage, Project, Publication


def _clip_of(job: Job) -> str | None:
    return (job.payload or {}).get("clip_id")


def project_costs(db: Session, project_id: uuid.UUID) -> dict:
    clips = [c.id for c in db.execute(select(Clip).where(Clip.project_id == project_id)).scalars()]
    jobs = {j.id: j for j in db.execute(select(Job).where(Job.project_id == project_id)).scalars()}
    per_clip: dict[str, dict] = {str(c): {"ai_usd": 0.0, "ai_calls": 0, "processing_s": 0.0, "estimated": False} for c in clips}
    shared = {"ai_usd": 0.0, "ai_calls": 0, "processing_s": 0.0, "estimated": False}

    def bucket(job_id) -> dict:
        job = jobs.get(job_id) if job_id else None
        clip = _clip_of(job) if job else None
        return per_clip.get(clip) if clip in per_clip else shared

    for usage in db.execute(select(AIUsage).where(AIUsage.project_id == project_id)).scalars():
        target = bucket(usage.job_id)
        target["ai_usd"] += usage.cost_usd or 0.0
        target["ai_calls"] += 1
        target["estimated"] = target["estimated"] or bool(usage.cost_estimated)
    for job in jobs.values():
        if job.started_at and job.finished_at:
            bucket(job.id)["processing_s"] += max(0.0, (job.finished_at - job.started_at).total_seconds())
    n = max(1, len(clips))
    total = {"ai_usd": round(sum(v["ai_usd"] for v in per_clip.values()) + shared["ai_usd"], 6),
             "ai_calls": sum(v["ai_calls"] for v in per_clip.values()) + shared["ai_calls"],
             "processing_s": round(sum(v["processing_s"] for v in per_clip.values()) + shared["processing_s"], 1),
             "estimated": shared["estimated"] or any(v["estimated"] for v in per_clip.values())}
    out = {}
    for clip_id, own in per_clip.items():
        out[clip_id] = {"ai_usd": round(own["ai_usd"] + shared["ai_usd"] / n, 6),
                        "ai_calls_own": own["ai_calls"], "ai_calls_shared": shared["ai_calls"],
                        "processing_s": round(own["processing_s"] + shared["processing_s"] / n, 1),
                        "estimated": own["estimated"] or shared["estimated"]}
    return {"total": total, "clips": out, "shared_by": n}


def channel_costs(db: Session) -> dict:
    """Totals across projects, and per published Short (with cost per 1,000 views when views exist)."""
    per_short = []
    totals = defaultdict(float)
    for project in db.execute(select(Project)).scalars():
        costs = project_costs(db, project.id)
        totals["ai_usd"] += costs["total"]["ai_usd"]
        totals["processing_s"] += costs["total"]["processing_s"]
        for clip_id, cost in costs["clips"].items():
            pubs = db.execute(select(Publication, OutputPackage).join(OutputPackage, OutputPackage.id == Publication.package_id)
                              .where(OutputPackage.clip_id == uuid.UUID(clip_id), Publication.status == "published")).all()
            for pub, package in pubs:
                share = cost["ai_usd"] / len(pubs)
                snap = db.execute(select(MetricSnapshot).where(MetricSnapshot.publication_id == pub.id)
                                  .order_by(MetricSnapshot.collected_at.desc())).scalars().first()
                views = (snap.metrics or {}).get("views") if snap else None
                per_short.append({"publication_id": str(pub.id), "platform": pub.platform,
                                  "title": package.title or (package.caption or "")[:80], "ai_usd": round(share, 6),
                                  "processing_s": round(cost["processing_s"] / len(pubs), 1), "views": views,
                                  "usd_per_1k_views": round(share / views * 1000, 6) if views else None,
                                  "estimated": cost["estimated"]})
    published = len(per_short)
    return {"ai_usd": round(totals["ai_usd"], 6), "processing_s": round(totals["processing_s"], 1),
            "published": published,
            "avg_ai_usd_per_short": round(sum(s["ai_usd"] for s in per_short) / published, 6) if published else None,
            "shorts": sorted(per_short, key=lambda s: -s["ai_usd"])[:100]}
