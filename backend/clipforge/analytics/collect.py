"""Collect authorised performance metrics (Part 17). Never invents a value.

Only metrics the platform returned are stored; everything else is recorded as
*unavailable* with the reason, so comparisons and learning work only from
real numbers.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..db.base import utcnow
from ..db.models import Clip, MetricSnapshot, OutputPackage, Publication, Render, SocialAccount
from ..platforms import google, instagram, youtube
from ..platforms.http import PlatformError

log = logging.getLogger(__name__)

YT_ANALYTICS_METRICS = ["views", "estimatedMinutesWatched", "averageViewDuration", "averageViewPercentage", "likes",
                        "comments", "shares", "subscribersGained"]
YT_NOT_IN_API = {
    "impressions": "not exposed by the YouTube Analytics API (YouTube Studio only)",
    "impressionClickThroughRate": "not exposed by the YouTube Analytics API (YouTube Studio only)",
    "saves": "YouTube does not report saves",
    "viewed_rate": "YouTube Studio only (\"viewed vs. swiped away\") — import a Studio CSV to add it",
}


def collect(db: Session, publication: Publication) -> MetricSnapshot:
    if publication.status != "published" or not publication.external_id:
        raise ValueError("Only confirmed publications have analytics")
    account = db.get(SocialAccount, publication.account_id) if publication.account_id else None
    if account is None or account.status != "connected":
        raise PlatformError(publication.platform, "The publishing account is no longer connected.", reason="reauth")
    age_hours = round((utcnow() - publication.published_at).total_seconds() / 3600, 2)
    if publication.platform == "youtube":
        metrics, unavailable, source = _youtube(db, account, publication)
    else:
        metrics, unavailable, source = _instagram(db, account, publication)
    snapshot = MetricSnapshot(publication_id=publication.id, platform=publication.platform, age_hours=age_hours,
                              metrics=metrics, unavailable=unavailable, source=source)
    db.add(snapshot)
    db.flush()
    return snapshot


def _youtube(db: Session, account: SocialAccount, publication: Publication) -> tuple[dict, dict, str]:
    auth = youtube.Auth(access_token=google.access_token(db, account))
    metrics: dict[str, float] = {}
    unavailable: dict[str, str] = dict(YT_NOT_IN_API)
    item = youtube.statistics(auth, [publication.external_id]).get(publication.external_id)
    google.record_quota(db, "videos.list")
    if item is None:
        unavailable["statistics"] = "video not found (deleted or private)"
    else:
        for key, name in (("viewCount", "views"), ("likeCount", "likes"), ("commentCount", "comments")):
            value = (item.get("statistics") or {}).get(key)
            if value is not None:
                metrics[name] = float(value)
            else:
                unavailable[name] = "hidden by the channel or not reported"
    start = publication.published_at.astimezone(UTC).date().isoformat()
    end = datetime.now(UTC).date().isoformat()
    try:
        report = youtube.analytics_report(auth, publication.external_id, start, end, YT_ANALYTICS_METRICS)
        headers = [h["name"] for h in report.get("columnHeaders", [])]
        rows = report.get("rows") or []
        if rows:
            for name, value in zip(headers, rows[0]):
                if name != "video" and isinstance(value, (int, float)):
                    metrics[f"analytics_{name}"] = float(value)
        else:
            for name in YT_ANALYTICS_METRICS:
                unavailable[f"analytics_{name}"] = "YouTube Analytics has no data yet (typically 24–72 h delay)"
    except PlatformError as exc:
        for name in YT_ANALYTICS_METRICS:
            unavailable[f"analytics_{name}"] = f"Analytics API unavailable: {str(exc)[:120]}"
    _youtube_shorts(db, auth, publication, start, end, metrics, unavailable)
    return metrics, unavailable, "youtube_data_api+youtube_analytics_api"


def _youtube_shorts(db: Session, auth, publication: Publication, start: str, end: str, metrics: dict, unavailable: dict) -> None:
    """Early retention: views past the first seconds and the audience-retention curve (retention
    at 3 s, at the end, replays). Asked separately so one unsupported report never loses the rest."""
    from . import retention

    try:
        report = youtube.analytics_report(auth, publication.external_id, start, end, ["engagedViews"])
        rows = report.get("rows") or []
        headers = [h["name"] for h in report.get("columnHeaders", [])]
        value = dict(zip(headers, rows[0], strict=False)).get("engagedViews") if rows else None
        if isinstance(value, (int, float)):
            metrics["analytics_engagedViews"] = float(value)
        else:
            unavailable["analytics_engagedViews"] = "YouTube Analytics has no data yet (typically 24–72 h delay)"
    except PlatformError as exc:
        unavailable["analytics_engagedViews"] = f"Analytics API unavailable: {str(exc)[:120]}"
    try:
        curve = youtube.retention_curve(auth, publication.external_id, start, end)
    except PlatformError as exc:
        unavailable["first_3s_retention"] = f"retention report unavailable: {str(exc)[:120]}"
        return
    package = db.get(OutputPackage, publication.package_id)
    render = db.get(Render, package.render_id) if package and package.render_id else None
    kept = (render.edit_decisions or {}).get("kept_intervals") if render else None
    clip = db.get(Clip, package.clip_id) if package else None
    duration = sum(b - a for a, b in kept) if kept else ((clip.end_s - clip.start_s) if clip else 0.0)
    derived = retention.curve_metrics(curve, duration)
    if derived:
        metrics.update(derived)
    else:
        for name in ("first_3s_retention", "end_retention", "replay_ratio"):
            unavailable[name] = "no audience-retention data yet (YouTube needs enough views and 24–72 h)"


def _instagram(db: Session, account: SocialAccount, publication: Publication) -> tuple[dict, dict, str]:
    token = instagram.access_token(db, account)
    metrics, unavailable = instagram.reel_insights(token, publication.external_id)
    unavailable.setdefault("impressions", "deprecated for Reels by the Instagram API")
    return metrics, unavailable, "instagram_insights"
