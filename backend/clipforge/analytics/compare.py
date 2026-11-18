"""Performance comparison (Part 17): platform-specific, normalised, honest.

Each published clip is scored relative to the median of its own platform
(log ratio), so a big and a small channel can be compared fairly and views are
not the only signal (engagement rate and retention are reported alongside).
Group comparisons use a permutation test and always report sample sizes.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Clip, MetricSnapshot, OutputPackage, Publication, Render
from ..ingestion.urls import display_name


@dataclass
class Row:
    publication_id: str
    platform: str
    title: str
    published_at: Any
    age_hours: float
    views: float | None
    engagement_rate: float | None
    retention: float | None
    duration: float
    hook: str
    features: dict[str, Any]
    scores: dict[str, float]
    caption_style: str | None
    strategy_id: str | None
    variant: str | None
    relative: float | None = None
    metrics: dict[str, float] = field(default_factory=dict)


def _views(platform: str, metrics: dict[str, float]) -> float | None:
    if platform == "youtube":
        return metrics.get("views", metrics.get("analytics_views"))
    return metrics.get("views", metrics.get("reach"))


def _engagement(platform: str, metrics: dict[str, float], views: float | None) -> float | None:
    if not views:
        return None
    keys = ("likes", "comments", "shares") if platform == "youtube" else ("likes", "comments", "shares", "saved")
    present = [metrics[k] for k in keys if k in metrics]
    return round(sum(present) / views, 5) if present else None


def _retention(platform: str, metrics: dict[str, float]) -> float | None:
    if platform == "youtube":
        return metrics.get("analytics_averageViewPercentage")
    return metrics.get("ig_reels_avg_watch_time")


def _rendered_duration(render: Render | None, clip: Clip) -> float:
    kept = (render.edit_decisions or {}).get("kept_intervals") if render else None
    if kept:
        return round(sum(b - a for a, b in kept), 2)
    return round(clip.end_s - clip.start_s, 2)


def dataset(db: Session, platform: str | None = None, *, min_age_hours: float = 20.0) -> list[Row]:
    query = (select(Publication, OutputPackage, Clip, Render)
             .join(OutputPackage, OutputPackage.id == Publication.package_id)
             .join(Clip, Clip.id == OutputPackage.clip_id)
             .join(Render, Render.id == OutputPackage.render_id, isouter=True)
             .where(Publication.status == "published"))
    if platform:
        query = query.where(Publication.platform == platform)
    rows: list[Row] = []
    for publication, package, clip, render in db.execute(query).all():
        snapshot = db.execute(select(MetricSnapshot).where(MetricSnapshot.publication_id == publication.id)
                              .order_by(MetricSnapshot.collected_at.desc())).scalars().first()
        if snapshot is None or (snapshot.age_hours or 0) < min_age_hours:
            continue
        views = _views(publication.platform, snapshot.metrics)
        rows.append(Row(
            publication_id=str(publication.id), platform=publication.platform, title=package.title or clip.title,
            published_at=publication.published_at, age_hours=snapshot.age_hours or 0.0, views=views,
            engagement_rate=_engagement(publication.platform, snapshot.metrics, views),
            retention=_retention(publication.platform, snapshot.metrics),
            duration=_rendered_duration(render, clip),
            hook=clip.hook, features=clip.features or {}, scores=clip.scores or {},
            caption_style=(render.edit_decisions or {}).get("caption_style") if render else None,
            strategy_id=str(clip.strategy_id) if clip.strategy_id else None, variant=clip.experiment_variant,
            metrics=snapshot.metrics,
        ))
    for plat in {r.platform for r in rows}:
        values = [r.views for r in rows if r.platform == plat and r.views is not None]
        if not values:
            continue
        median = float(np.median(values))
        for r in rows:
            if r.platform == plat and r.views is not None:
                r.relative = math.log((r.views + 1) / (median + 1))
    return rows


def permutation_test(a: list[float], b: list[float], *, iterations: int = 4000, seed: int = 7) -> tuple[float, float]:
    """Two-sided permutation test on the difference of means. Returns (diff, p)."""
    observed = float(np.mean(a) - np.mean(b))
    pooled = list(a) + list(b)
    rng = random.Random(seed)
    extreme = 0
    for _ in range(iterations):
        rng.shuffle(pooled)
        diff = np.mean(pooled[:len(a)]) - np.mean(pooled[len(a):])
        if abs(diff) >= abs(observed) - 1e-12:
            extreme += 1
    return observed, (extreme + 1) / (iterations + 1)


def summary(db: Session, platform: str | None = None) -> dict[str, Any]:
    rows = dataset(db, platform, min_age_hours=0.0)
    out: dict[str, Any] = {"platforms": {}}
    lines: list[str] = []
    for plat in sorted({r.platform for r in rows}):
        prow = [r for r in rows if r.platform == plat and r.views is not None]
        if not prow:
            continue
        ranked = sorted(prow, key=lambda r: r.views, reverse=True)
        median = float(np.median([r.views for r in prow]))
        engagement = [r.engagement_rate for r in prow if r.engagement_rate is not None]
        out["platforms"][plat] = {
            "published_with_data": len(prow), "median_views": median,
            "median_engagement_rate": float(np.median(engagement)) if engagement else None,
            "top": [{"title": r.title, "views": r.views, "age_hours": r.age_hours} for r in ranked[:3]],
            "bottom": [{"title": r.title, "views": r.views, "age_hours": r.age_hours} for r in ranked[-3:]],
        }
        lines.append(f"{display_name(plat)}: {len(prow)} video(s) with data; median {median:,.0f} views; best so far “{ranked[0].title}” "
                     f"({ranked[0].views:,.0f} views at {ranked[0].age_hours:.0f}h).")
    out["text"] = "\n".join(lines) or "Published videos don't have analytics yet."
    return out
