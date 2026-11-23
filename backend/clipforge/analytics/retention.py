"""Early-retention feedback for Shorts (P2): did people stay past the first seconds, how much of
the clip did they watch, did it replay — not just views.

Where the numbers come from (never invented):

* **YouTube Analytics API** (collected automatically): the audience-retention curve
  (``audienceWatchRatio`` by elapsed share of the video) gives retention at 3 s, at the end, and
  replays (a ratio above 1); ``engagedViews`` (views past the first seconds) next to ``views``;
  ``averageViewPercentage``. The API usually has no data for the first 24-72 hours.
* **YouTube Studio's "viewed vs. swiped away"** is not offered by the API: import it from a Studio
  CSV export (or type it in). Same for anything else the APIs don't give.
* **Instagram**: Reels insights (average watch time, views, saves, shares).

From these, each published clip gets early-retention signals and flags ("flop: 38 % viewed"),
hook/title styles are ranked by what kept viewers, and the source segments that made strong
openings are listed. Suggestions only: nothing is deleted or re-edited automatically.
"""

from __future__ import annotations

import csv
import io
import re
import statistics
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.base import utcnow
from ..db.models import Clip, MetricSnapshot, OutputPackage, Publication, Render

#: Canonical metric names written by the importer and the curve parser.
SIGNALS = ("viewed_rate", "swipe_away_rate", "first_3s_retention", "end_retention", "replay_ratio",
           "average_percentage_viewed", "engaged_view_rate")

# ------------------------------------------------------------------ YouTube curve


def curve_metrics(curve: list[tuple[float, float, float | None]], duration_s: float) -> dict[str, float]:
    """Retention at 3 s and at the end, and the replay signal, from the retention report."""
    if not curve or duration_s <= 0:
        return {}
    target = min(1.0, 3.0 / duration_s)
    at3 = min(curve, key=lambda p: abs(p[0] - target))
    out = {"first_3s_retention": round(at3[1], 4), "end_retention": round(curve[-1][1], 4),
           "replay_ratio": round(max(p[1] for p in curve), 4)}
    if at3[2] is not None:
        out["first_3s_relative_retention"] = round(at3[2], 4)
    return out


# ------------------------------------------------------------------ CSV / manual import

#: Column names (lower-case, as YouTube Studio and Meta exports name them, plus plain names).
ALIASES = {
    "external_id": ("content", "video id", "video_id", "id", "media id", "media_id", "reel id", "post id"),
    "title": ("video title", "title", "caption", "description"),
    "viewed_rate": ("stayed to watch (%)", "viewed (%)", "viewed vs. swiped away (%)", "viewed vs swiped away (%)",
                    "viewed vs. swiped away", "viewed", "viewed_rate", "stayed to watch"),
    "swipe_away_rate": ("swiped away (%)", "swiped away", "swipe away rate", "swipe_away_rate", "skip rate", "skip rate (%)"),
    "average_percentage_viewed": ("average percentage viewed (%)", "average percentage viewed", "avg percentage viewed",
                                  "average_percentage_viewed", "average view percentage"),
    "first_3s_retention": ("retention at 3s (%)", "first 3s retention (%)", "3 second retention", "first_3s_retention"),
    "replay_ratio": ("replay ratio", "replays per view", "replay_ratio"),
    "views": ("views", "plays"),
    "engaged_views": ("engaged views", "engaged_views"),
    "average_view_duration_s": ("average view duration", "average_view_duration", "avg watch time", "average watch time"),
}
RATE_KEYS = {"viewed_rate", "swipe_away_rate", "first_3s_retention"}


def _number(raw: str, key: str) -> float | None:
    text = (raw or "").strip().replace(",", "")
    if not text or text in {"-", "—", "n/a", "N/A"}:
        return None
    if key == "average_view_duration_s" and ":" in text:
        parts = [float(p) for p in text.split(":")]
        seconds = 0.0
        for part in parts:
            seconds = seconds * 60 + part
        return seconds
    pct = text.endswith("%")
    try:
        value = float(text.rstrip("%"))
    except ValueError:
        return None
    if key in RATE_KEYS and (pct or value > 1.0):
        value /= 100.0  # stored as 0-1
    return value


@dataclass
class ImportResult:
    imported: int
    unmatched: list[str]
    skipped: list[str]
    columns: dict[str, str]

    def to_dict(self) -> dict:
        return {"imported": self.imported, "unmatched": self.unmatched, "skipped": self.skipped, "columns": self.columns}


def _find_publication(db: Session, platform: str | None, external_id: str | None, title: str | None) -> Publication | None:
    query = select(Publication).where(Publication.status == "published")
    if platform:
        query = query.where(Publication.platform == platform)
    if external_id:
        pub = db.execute(query.where(Publication.external_id == external_id.strip())).scalars().first()
        if pub is not None:
            return pub
    if title:
        clean = re.sub(r"\s*#shorts\s*$", "", title.strip(), flags=re.IGNORECASE).lower()
        for pub, package in db.execute(query.add_columns(OutputPackage)
                                       .join(OutputPackage, OutputPackage.id == Publication.package_id)).all():
            names = {re.sub(r"\s*#shorts\s*$", "", (package.title or ""), flags=re.IGNORECASE).strip().lower(),
                     (package.caption or "").split("\n")[0].strip().lower()}
            if clean and clean in names:
                return pub
    return None


def import_rows(db: Session, rows: list[dict[str, str]], *, platform: str | None = None, source: str = "csv_import") -> ImportResult:
    """Store imported metrics for published clips. Only columns present with a number are stored;
    rows that match no published clip are reported back, not guessed."""
    if not rows:
        return ImportResult(0, [], [], {})
    headers = {h.strip().lower(): h for h in rows[0]}
    mapping: dict[str, str] = {}
    for key, names in ALIASES.items():
        for alias in names:
            if alias in headers:
                mapping[key] = headers[alias]
                break
    columns = mapping
    imported, unmatched, skipped = 0, [], []
    for n, row in enumerate(rows, start=2):
        ext = row.get(columns.get("external_id", ""), "") if "external_id" in columns else ""
        title = row.get(columns.get("title", ""), "") if "title" in columns else ""
        label = ext or title or f"row {n}"
        if (ext or title).strip().lower() in {"total", "totals", ""}:
            skipped.append(f"row {n}: no video id or title")
            continue
        pub = _find_publication(db, platform, ext, title)
        if pub is None:
            unmatched.append(label[:80])
            continue
        metrics = {}
        for key, column in columns.items():
            if key in {"external_id", "title"}:
                continue
            value = _number(row.get(column, ""), key)
            if value is not None:
                metrics[key] = round(value, 4)
        if "viewed_rate" in metrics and "swipe_away_rate" not in metrics:
            metrics["swipe_away_rate"] = round(1 - metrics["viewed_rate"], 4)
        elif "swipe_away_rate" in metrics and "viewed_rate" not in metrics:
            metrics["viewed_rate"] = round(1 - metrics["swipe_away_rate"], 4)
        if not metrics:
            skipped.append(f"{label[:60]}: no numbers in the known columns")
            continue
        age = round((utcnow() - pub.published_at).total_seconds() / 3600, 2) if pub.published_at else None
        db.add(MetricSnapshot(publication_id=pub.id, platform=pub.platform, age_hours=age, metrics=metrics,
                              unavailable={}, source=source[:60]))
        imported += 1
    db.flush()
    return ImportResult(imported, unmatched, skipped, columns)


def parse_csv(data: bytes | str) -> list[dict[str, str]]:
    text = data.decode("utf-8-sig", errors="replace") if isinstance(data, bytes) else data
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [dict(r) for r in csv.DictReader(io.StringIO(text), dialect=dialect)]


# ------------------------------------------------------------------ signals and flags


def latest_metrics(db: Session, publication_id) -> tuple[dict[str, float], list[str]]:
    """All snapshots merged, newest value wins; with the sources they came from."""
    merged: dict[str, float] = {}
    sources: list[str] = []
    for snap in db.execute(select(MetricSnapshot).where(MetricSnapshot.publication_id == publication_id)
                           .order_by(MetricSnapshot.collected_at)).scalars():
        merged.update(snap.metrics or {})
        if snap.source not in sources:
            sources.append(snap.source)
    return merged, sources


def signals(metrics: dict[str, float], platform: str, duration_s: float | None = None) -> dict[str, float]:
    out = {k: metrics[k] for k in SIGNALS if k in metrics}
    if "average_percentage_viewed" not in out and platform == "instagram" and duration_s \
            and "ig_reels_avg_watch_time" in metrics:  # milliseconds per play
        out["average_percentage_viewed"] = round(metrics["ig_reels_avg_watch_time"] / 1000 / duration_s * 100, 1)
    if "average_percentage_viewed" not in out and platform == "youtube" and "analytics_averageViewPercentage" in metrics:
        out["average_percentage_viewed"] = metrics["analytics_averageViewPercentage"]
    views = metrics.get("analytics_views") or metrics.get("views")
    engaged = metrics.get("analytics_engagedViews") or metrics.get("engaged_views")
    if "engaged_view_rate" not in out and views and engaged is not None:
        out["engaged_view_rate"] = round(min(1.0, engaged / views), 4)
    return out


def flags(sig: dict[str, float], cfg, *, age_hours: float | None) -> tuple[list[str], str | None]:
    """Flop flags from real numbers only, and what to do about it (a suggestion)."""
    out: list[str] = []
    if "viewed_rate" in sig and sig["viewed_rate"] < cfg.flop_viewed_rate:
        out.append(f"only {sig['viewed_rate']:.0%} viewed (the rest swiped away; flag below {cfg.flop_viewed_rate:.0%})")
    if "first_3s_retention" in sig and sig["first_3s_retention"] < cfg.flop_first_3s_retention:
        out.append(f"{sig['first_3s_retention']:.0%} still watching at 3 s (flag below {cfg.flop_first_3s_retention:.0%})")
    if "average_percentage_viewed" in sig and sig["average_percentage_viewed"] < cfg.flop_avg_viewed_pct:
        out.append(f"{sig['average_percentage_viewed']:.0f}% of the clip watched on average (flag below {cfg.flop_avg_viewed_pct:.0f}%)")
    if not out:
        return out, None
    early = any("viewed" in f or "at 3 s" in f for f in out)
    very_low = sig.get("viewed_rate", 1) < cfg.flop_viewed_rate * 0.6 or sig.get("first_3s_retention", 2) < cfg.flop_first_3s_retention * 0.6
    if very_low and (age_hours or 0) >= 48:
        return out, "consider deleting it or re-posting with a new opening"
    return out, "re-edit the opening (first 3 seconds)" if early else "tighten the middle or shorten it"


def primary(sig: dict[str, float]) -> tuple[str, float] | None:
    """The best available early-retention number for ranking (Studio's viewed rate first)."""
    for key in ("viewed_rate", "first_3s_retention", "engaged_view_rate", "average_percentage_viewed"):
        if key in sig:
            return key, sig[key]
    return None


def _rows(db: Session) -> list[tuple[Publication, OutputPackage, Clip, Render | None]]:
    return db.execute(select(Publication, OutputPackage, Clip, Render)
                      .join(OutputPackage, OutputPackage.id == Publication.package_id)
                      .join(Clip, Clip.id == OutputPackage.clip_id)
                      .join(Render, Render.id == OutputPackage.render_id, isouter=True)
                      .where(Publication.status == "published")).all()


def styles_of(clip: Clip, package: OutputPackage) -> dict[str, str]:
    from ..shorts import originality

    shorts = clip.shorts or {}
    variants = shorts.get("packaging_variants") or []
    title = (package.title or (package.caption or "").split("\n")[0]).replace("#shorts", "").strip()
    used = next((v for v in variants if v.get("title") == title), None)
    return {
        "hook_type": originality.hook_type(clip.hook or ""),
        "title_template": used["template"] if used else "other",
        "first_frame_text": "yes" if shorts.get("first_frame_text") or (shorts.get("hook") or {}).get("text_hook") else "no",
        "rehook": (shorts.get("retention_plan") or {}).get("text_kind") or "none",
        "opening": (shorts.get("hook") or {}).get("status", "not scored"),
    }


def evaluate(db: Session, cfg) -> dict:
    """Update every published clip's early-retention signals and flags; rank styles; list strong
    source segments. Returns the report shown in the app."""
    per_platform: dict[str, list[float]] = {}
    entries = []
    for pub, package, clip, render in _rows(db):
        metrics, sources = latest_metrics(db, pub.id)
        kept = (render.edit_decisions or {}).get("kept_intervals") if render else None
        duration = sum(b - a for a, b in kept) if kept else clip.end_s - clip.start_s
        sig = signals(metrics, pub.platform, duration)
        age = round((utcnow() - pub.published_at).total_seconds() / 3600, 1) if pub.published_at else None
        found, suggestion = flags(sig, cfg, age_hours=age)
        analytics = dict((clip.shorts or {}).get("analytics") or {})
        analytics[pub.platform] = {"signals": sig, "flags": found, "suggestion": suggestion, "sources": sources,
                                   "age_hours": age, "updated_at": utcnow().isoformat(),
                                   "missing": [k for k in ("viewed_rate", "first_3s_retention") if k not in sig]}
        clip.shorts = {**(clip.shorts or {}), "analytics": analytics}
        main = primary(sig)
        if main:
            per_platform.setdefault(f"{pub.platform}:{main[0]}", []).append(main[1])
        entries.append((pub, package, clip, sig, main, found, suggestion))
    # Normalise within platform+metric so a YouTube viewed-rate and an Instagram watch share mix fairly.
    def z(platform: str, main) -> float | None:
        values = per_platform.get(f"{platform}:{main[0]}", [])
        if len(values) < 2:
            return None
        sd = statistics.pstdev(values) or 1e-9
        return (main[1] - statistics.mean(values)) / sd

    groups: dict[str, dict[str, list[float]]] = {}
    segments: dict[str, list[dict]] = {}
    flops = []
    for pub, package, clip, sig, main, found, suggestion in entries:
        if found:
            flops.append({"clip_id": str(clip.id), "publication_id": str(pub.id), "platform": pub.platform,
                          "title": package.title or (package.caption or "")[:80], "flags": found, "suggestion": suggestion,
                          "url": pub.external_url})
        if not main:
            continue
        score = z(pub.platform, main)
        for dim, value in styles_of(clip, package).items():
            if score is not None:
                groups.setdefault(dim, {}).setdefault(value, []).append(score)
        segments.setdefault(str(clip.source_id), []).append({
            "clip_id": str(clip.id), "start_s": clip.start_s, "end_s": clip.end_s, "hook": clip.hook,
            "platform": pub.platform, "metric": main[0], "value": main[1]})
    ranking = []
    for dim, values in groups.items():
        for value, scores in values.items():
            ranking.append({"dimension": dim, "style": value, "posts": len(scores),
                            "relative_retention": round(statistics.mean(scores), 3),
                            "enough_data": len(scores) >= 3})
    ranking.sort(key=lambda r: (r["dimension"], -r["relative_retention"]))
    strong = {src: sorted(items, key=lambda i: -i["value"])[:5] for src, items in segments.items()}
    return {"ranking": ranking, "segments": strong, "flops": flops, "clips": len(entries),
            "with_retention": sum(1 for e in entries if e[4]),
            "note": "Rankings use only clips with real retention numbers; a style needs 3+ posts before it counts."}


def template_boost(db: Session, cfg) -> dict[str, float]:
    """Learned nudge for P1 title-option ranking: +-0.1 at most, only for templates with 3+ posts."""
    report = evaluate(db, cfg)
    return {r["style"]: round(max(-0.1, min(0.1, 0.05 * r["relative_retention"])), 3)
            for r in report["ranking"] if r["dimension"] == "title_template" and r["enough_data"]}


def manual_entry(db: Session, publication_id: uuid.UUID, values: dict[str, float]) -> MetricSnapshot:
    """Numbers typed in by the owner (e.g. read off YouTube Studio)."""
    pub = db.get(Publication, publication_id)
    if pub is None or pub.status != "published":
        raise ValueError("Only published clips have analytics.")
    metrics = {}
    for key, value in values.items():
        if key not in ALIASES or key in {"external_id", "title"}:
            raise ValueError(f"Unknown metric {key!r}.")
        metrics[key] = float(value) / 100.0 if key in RATE_KEYS and float(value) > 1 else float(value)
    if "viewed_rate" in metrics and "swipe_away_rate" not in metrics:
        metrics["swipe_away_rate"] = round(1 - metrics["viewed_rate"], 4)
    snap = MetricSnapshot(publication_id=pub.id, platform=pub.platform, metrics=metrics, unavailable={}, source="manual",
                          age_hours=round((utcnow() - pub.published_at).total_seconds() / 3600, 2) if pub.published_at else None)
    db.add(snap)
    db.flush()
    return snap
