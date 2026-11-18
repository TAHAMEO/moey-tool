"""The Brain's video-production workflow (LangGraph, checkpointed per job).

resolve ─┬─ full media / replay ─► acquire ► prepare ► (transcribe ∥ analyze) ► select ► render ► package ► publish_gate ► publish ► finalize
         ├─ transcript only ────► acquire ► transcript_moments ► finalize
         ├─ waiting for live ───► await_live (interrupt) ─► resolve
         └─ no usable access ───► report_access ► finalize

Heavy steps run as child jobs on specialist queues; the graph suspends with
LangGraph interrupts while they run and resumes from its checkpoint, so the
Brain keeps the whole execution state and nothing restarts from scratch.
"""

from __future__ import annotations

import logging
import operator
import uuid
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy import select

from ..brain.runtime import (
    ChildSpec,
    await_approval,
    await_children,
    get_checkpointer,
    job_context,
    run_graph_job,
)
from ..db.base import session_scope, utcnow
from ..db.models import Clip, MediaAsset, OutputPackage, Project, Render, Source
from ..ingestion import acquire as acq
from ..ingestion import resolver as rs
from ..jobs.registry import JobContext, PermanentError, RetryableError, handler
from ..media import highlights, metadata
from ..services import app_settings
from .media_jobs import load_analysis, load_words

log = logging.getLogger(__name__)


class VideoState(TypedDict, total=False):
    project_id: str
    source_id: str
    access: dict
    acquired: dict
    prepared: dict
    transcript: dict
    clip_ids: list[str]
    packages: list[dict]
    approved: list[str]
    published: list[dict]
    outcome: str
    notes: Annotated[list[str], operator.add]


# ------------------------------------------------------------------ helpers
def _set_project(project_id: str, *, status: str | None = None, label: str | None = None, **fields: Any) -> None:
    with session_scope() as db:
        project = db.get(Project, uuid.UUID(project_id))
        if status:
            project.status = status
        if label:
            project.status_label = label
        for key, value in fields.items():
            setattr(project, key, value)


def _progress(config, fraction: float, label: str, stage: str, message: str | None = None) -> None:
    ctx = job_context(config)
    ctx.check()
    ctx.progress(fraction, label=label, stage=stage, message=message)


def _fail(state: VideoState, message: str, *, code: str, label: str = "Failed") -> None:
    with session_scope() as db:
        project = db.get(Project, uuid.UUID(state["project_id"]))
        project.status = "failed"
        project.status_label = label
        project.errors = [*project.errors, {"code": code, "message": message, "at": utcnow().isoformat()}]
    raise PermanentError(message, code=code, label=label)


# -------------------------------------------------------------------- nodes
def resolve_node(state: VideoState, config) -> VideoState:
    _progress(config, 0.03, "Checking access", "resolve")
    _set_project(state["project_id"], status="running", label="Checking access")
    with session_scope() as db:
        source = db.get(Source, uuid.UUID(state["source_id"]))
        result = rs.resolve(db, source)
        acq.mark_resolved(source, result)
        if result.metadata.get("title"):
            project = db.get(Project, uuid.UUID(state["project_id"]))
            if project.request.get("placeholder_title"):
                project.title = result.metadata["title"][:300]
                project.request = {**project.request, "placeholder_title": False}
    if result.retryable and result.state == rs.SOURCE_UNAVAILABLE:
        raise RetryableError("; ".join(result.reasons) or "Source temporarily unreachable")
    job_context(config).event(f"Access: {result.state} ({result.method or 'no media method'})", stage="resolve",
                              data={"checks": result.checks})
    return {"access": result.to_dict(), "notes": [f"Access check: {result.state}"]}


def route_access(state: VideoState) -> str:
    access = state["access"]
    if access["state"] in (rs.FULL_MEDIA_ACCESS, rs.REPLAY_ACCESSIBLE):
        return "acquire"
    if access["state"] == rs.TRANSCRIPT_ONLY:
        return "acquire"
    if access["state"] == rs.AUTHORIZED_LIVE_FEED:
        return "live"
    if access.get("acquire", {}).get("await_live"):
        return "await_live"
    return "report_access"


def acquire_node(state: VideoState, config) -> VideoState:
    _progress(config, 0.08, "Fetching available source data", "acquire")
    _set_project(state["project_id"], label="Fetching available source data")
    outcome = await_children(config, "acquire", [ChildSpec("acquire", "source.acquire",
                                                           {"source_id": state["source_id"], "access": state["access"]},
                                                           queue="default", label="Fetching source")],
                             label="Fetching available source data")["acquire"]
    if not outcome.ok:
        error = (outcome.error or {}).get("message", "Could not fetch the source")
        _fail(state, error, code="acquire", label="Source unavailable")
    return {"acquired": outcome.result or {}}


def route_after_acquire(state: VideoState) -> str:
    return "transcript_moments" if state["access"]["state"] == rs.TRANSCRIPT_ONLY else "prepare"


def prepare_node(state: VideoState, config) -> VideoState:
    _progress(config, 0.15, "Preparing media", "prepare")
    outcome = await_children(config, "prepare", [ChildSpec("prepare", "media.prepare", {"source_id": state["source_id"]},
                                                           queue="media", label="Preparing media")],
                             label="Preparing media")["prepare"]
    if not outcome.ok:
        _fail(state, (outcome.error or {}).get("message", "Media preparation failed"), code="prepare",
              label="Source unavailable")
    return {"prepared": outcome.result}


def transcribe_analyze_node(state: VideoState, config) -> VideoState:
    _progress(config, 0.25, "Transcribing", "transcribe")
    _set_project(state["project_id"], label="Transcribing")
    with session_scope() as db:
        language = (db.get(Project, uuid.UUID(state["project_id"])).request.get("languages") or ["en"])[0]
    outcomes = await_children(config, "understand", [
        ChildSpec("transcribe", "media.transcribe", {"source_id": state["source_id"], "language": language},
                  queue="media", max_attempts=4, label="Transcribing"),
        ChildSpec("analyze", "media.analyze", {"source_id": state["source_id"],
                                               "has_video": state["prepared"].get("has_video", True)},
                  queue="media", label="Analyzing"),
    ], label="Transcribing and analyzing")
    if not outcomes["transcribe"].ok:
        _fail(state, (outcomes["transcribe"].error or {}).get("message", "Transcription failed"), code="transcribe")
    notes = []
    if not outcomes["analyze"].ok:
        notes.append("Visual/audio analysis failed; clips were chosen from speech only.")
    return {"transcript": outcomes["transcribe"].result, "notes": notes}


def select_node(state: VideoState, config) -> VideoState:
    _progress(config, 0.55, "Finding highlights", "select")
    _set_project(state["project_id"], label="Finding highlights")
    ctx: JobContext = job_context(config)
    source_id = uuid.UUID(state["source_id"])
    words = load_words(source_id)
    analyses = load_analysis(source_id)
    profile = analyses.get("audio_profile") or {}
    prepared = state.get("prepared") or {}
    sigs = highlights.SourceSignals(
        duration=prepared.get("duration") or (words[-1]["end"] if words else 0.0),
        levels_db=profile.get("levels_db", []), silences=[tuple(s) for s in profile.get("silences", [])],
        scenes=analyses.get("scenes") if isinstance(analyses.get("scenes"), list) else [],
        visual=analyses.get("visual") if isinstance(analyses.get("visual"), list) else [],
        events=analyses.get("audio_events") if isinstance(analyses.get("audio_events"), list) else [],
        has_video=prepared.get("has_video", True),
    )
    with session_scope() as db:
        from ..learning.engine import assign_strategy

        project = db.get(Project, uuid.UUID(state["project_id"]))
        request = dict(project.request)
        strategy, variant = assign_strategy(db, project.id)  # randomized while an experiment runs
        ai = app_settings.get_section(db, "ai")
        params = strategy.params or {}
        strategy_id = strategy.id
        project.strategy_id = strategy_id
    interests = list(params.get("interests", []))
    try:
        from ..memory.service import get_memory

        user_id = str(ctx.user_id) if ctx.user_id else None
        interests += [m["memory"] for m in get_memory().recall("topics my audience cares about", user_id=user_id,
                                                               limit=3, min_score=0.4)]
    except Exception as exc:  # noqa: BLE001 - degrade gracefully; logged
        log.debug("Memory unavailable for interests: %s", exc)
    req = highlights.SelectionRequest(
        count=int(request.get("count", 5)), min_s=float(request.get("min_seconds", 15)),
        max_s=float(request.get("max_seconds", 59)), focus=request.get("focus"), interests=interests,
        weights={**highlights.DEFAULT_WEIGHTS, **params.get("weights", {})}, target_s=params.get("target_seconds"),
        llm_rerank=ai.llm_rerank_clips, job_id=ctx.job_id, project_id=uuid.UUID(state["project_id"]),
    )
    chosen, stats = highlights.select(words, sigs, req)
    if not chosen:
        _fail(state, "I couldn't find complete, self-contained moments in this source.", code="no_highlights")
    clip_ids = []
    with session_scope() as db:
        for existing in db.execute(select(Clip).where(Clip.project_id == uuid.UUID(state["project_id"]))).scalars():
            clip_ids.append(str(existing.id))
        if not clip_ids:
            for rank, cand in enumerate(sorted(chosen, key=lambda c: c.total, reverse=True), 1):
                start, end = highlights.padded_bounds(cand, words, sigs.duration)
                clip = Clip(project_id=uuid.UUID(state["project_id"]), source_id=source_id, rank=rank, start_s=start,
                            end_s=end, title=(cand.llm or {}).get("title") or "", hook=cand.hook,
                            transcript_text=cand.text, scores=cand.scores, total_score=cand.total,
                            reasons=cand.reasons + cand.adjusted, features=cand.features, scoring_method=cand.method,
                            status="selected", strategy_id=strategy_id, experiment_variant=variant)
                db.add(clip)
                db.flush()
                clip_ids.append(str(clip.id))
    ctx.event(f"Selected {len(clip_ids)} clip(s) from {stats['candidates']} candidate windows"
              + (f"; re-ranked with {stats['llm_model']}" if stats.get("llm_model") else " (local scoring)"), stage="select")
    return {"clip_ids": clip_ids}


def render_node(state: VideoState, config) -> VideoState:
    _progress(config, 0.65, "Editing clips", "render")
    _set_project(state["project_id"], label="Rendering")
    outcomes = await_children(config, "render", [
        ChildSpec(f"render-{cid}", "media.render", {"clip_id": cid}, queue="media", label="Rendering")
        for cid in state["clip_ids"]
    ], label="Rendering clips")
    failed = [k for k, o in outcomes.items() if not o.ok]
    if len(failed) == len(outcomes):
        _fail(state, "Rendering failed for every clip: " + str(next(iter(outcomes.values())).error), code="render")
    notes = [f"{len(failed)} clip(s) failed to render"] if failed else []
    return {"notes": notes}


def package_node(state: VideoState, config) -> VideoState:
    _progress(config, 0.88, "Writing titles and captions", "package")
    ctx = job_context(config)
    packages: list[dict] = []
    with session_scope() as db:
        project = db.get(Project, uuid.UUID(state["project_id"]))
        source = db.get(Source, project.source_id)
        brand = app_settings.get_section(db, "brand")
        publishing = app_settings.get_section(db, "publishing")
        platforms = project.request.get("platforms") or publishing.platforms
        owned = bool((source.access_detail or {}).get("owned")) or source.platform in ("upload", "gdrive")
        clips = db.execute(select(Clip).where(Clip.project_id == project.id).order_by(Clip.rank)).scalars().all()
        for clip in clips:
            renders = {r.aspect: r for r in db.execute(select(Render).where(Render.clip_id == clip.id, Render.status == "done")).scalars()}
            vertical = renders.get("9:16") or next(iter(renders.values()), None)
            if vertical is None:
                continue
            copy = metadata.generate(clip.transcript_text, clip.hook, channel=brand.channel_name,
                                     source_title=(source.source_metadata or {}).get("title"),
                                     source_url=source.normalized_url, owned_source=owned, llm_title=clip.title or None,
                                     job_id=ctx.job_id, project_id=project.id, allow_llm=True)  # hub enforces paid-API policy/budgets
            if not clip.title:
                clip.title = copy.youtube_title.replace(" #shorts", "")
            yt_thumb = db.execute(select(MediaAsset).where(MediaAsset.project_id == project.id, MediaAsset.kind == "thumbnail",
                                                           MediaAsset.info["clip_id"].astext == str(clip.id))).scalars().first()
            for platform in platforms:
                package = db.execute(select(OutputPackage).where(OutputPackage.clip_id == clip.id,
                                                                 OutputPackage.platform == platform)).scalar_one_or_none()
                if package is None:
                    package = OutputPackage(clip_id=clip.id, platform=platform)
                    db.add(package)
                package.render_id = vertical.id
                package.metadata_method = copy.method
                package.privacy = publishing.default_privacy
                if platform == "youtube":
                    package.title = copy.youtube_title
                    package.description = copy.youtube_description
                    package.tags = copy.youtube_tags
                    package.subtitle_asset_id = vertical.srt_asset_id
                    package.thumbnail_asset_id = yt_thumb.id if yt_thumb else vertical.thumbnail_asset_id
                else:
                    package.caption = copy.instagram_caption
                    package.hashtags = copy.instagram_hashtags
                    package.cover_asset_id = vertical.thumbnail_asset_id
                package.status = "ready"
                db.flush()
                packages.append({"package_id": str(package.id), "platform": platform, "rank": clip.rank,
                                 "title": clip.title, "clip_id": str(clip.id),
                                 "qc_passed": bool(vertical.quality_report.get("passed")),
                                 "review_words": len(vertical.subtitle_report.get("review_words", []))})
        project.status = "ready"
        project.status_label = "Clips ready"
        project.analysis_level = "full_media"
    return {"packages": packages}


def publish_gate_node(state: VideoState, config) -> VideoState:
    with session_scope() as db:
        project = db.get(Project, uuid.UUID(state["project_id"]))
        publishing = app_settings.get_section(db, "publishing")
        requested = bool(project.request.get("publish_requested"))
    packages = [p for p in state.get("packages", []) if p["platform"] in publishing.platforms]
    if not packages or publishing.policy == "draft_only" or not (requested or publishing.policy == "auto_publish"):
        return {"approved": []}
    blocked = [p for p in packages if not p["qc_passed"] or p["review_words"]]
    if publishing.policy == "auto_publish" and not blocked:
        return {"approved": [p["package_id"] for p in packages], "notes": ["Auto-publish policy: approved automatically"]}
    _set_project(state["project_id"], status="awaiting_approval", label="Waiting for your approval")
    decision = await_approval({"packages": packages, "project_id": state["project_id"],
                               "flags": [{"package_id": p["package_id"], "reason": "caption words need review"
                                          if p["review_words"] else "quality check warning"} for p in blocked]})
    approved = [pid for pid in (decision or {}).get("approved", []) if any(p["package_id"] == pid for p in packages)]
    return {"approved": approved}


def publish_node(state: VideoState, config) -> VideoState:
    if not state.get("approved"):
        return {"published": []}
    _progress(config, 0.92, "Publishing", "publish")
    _set_project(state["project_id"], status="publishing", label="Publishing")
    outcomes = await_children(config, "publish", [
        ChildSpec(f"publish-{pid}", "workflow.publish", {"package_id": pid}, queue="default", max_attempts=3,
                  label="Publishing") for pid in state["approved"]
    ], label="Publishing approved clips")
    published = [{"package_id": key.removeprefix("publish-"),
                  "status": "scheduled" if (o.result or {}).get("scheduled_at") else o.status,
                  "result": o.result, "error": o.error} for key, o in outcomes.items()]
    return {"published": published}


def transcript_moments_node(state: VideoState, config) -> VideoState:
    """Transcript-only access: find moments honestly, without claiming visual analysis or renders."""
    _progress(config, 0.5, "Analyzing", "transcript")
    words = load_words(uuid.UUID(state["source_id"]))
    with session_scope() as db:
        project = db.get(Project, uuid.UUID(state["project_id"]))
        request = dict(project.request)
    chosen, _stats = highlights.select(words, highlights.SourceSignals(duration=words[-1]["end"] if words else 0, has_video=False),
                                       highlights.SelectionRequest(count=int(request.get("count", 5)),
                                                                   min_s=float(request.get("min_seconds", 15)),
                                                                   max_s=float(request.get("max_seconds", 59)),
                                                                   focus=request.get("focus"), llm_rerank=False))
    with session_scope() as db:
        project = db.get(Project, uuid.UUID(state["project_id"]))
        for rank, cand in enumerate(chosen, 1):
            db.add(Clip(project_id=project.id, source_id=project.source_id, rank=rank, start_s=cand.start, end_s=cand.end,
                        hook=cand.hook, transcript_text=cand.text, scores=cand.scores, total_score=cand.total,
                        reasons=cand.reasons, features={k: v for k, v in cand.features.items() if k != "_vec"},
                        scoring_method="transcript_only", status="candidate"))
        project.analysis_level = "transcript_only"
        project.limitations = [("Only the caption transcript was available: moments were chosen from the words alone. "
                                "No visual analysis was performed and no clips could be rendered.")]
    return {"outcome": "transcript_only"}


def report_access_node(state: VideoState, config) -> VideoState:
    access = state["access"]
    label = {rs.SOURCE_UNAVAILABLE: "Source unavailable", rs.SOURCE_PRIVATE: "Source unavailable",
             rs.UNSUPPORTED_SOURCE: "Source unavailable"}.get(access["state"], "Access required")
    level = "metadata_only" if access["capabilities"].get("metadata") else "none"
    _set_project(state["project_id"], status="access_required" if label == "Access required" else "unavailable",
                 label=label, analysis_level=level, limitations=access["reasons"])
    return {"outcome": "access_limited"}


def await_live_node(state: VideoState, config) -> VideoState:
    _set_project(state["project_id"], status="waiting", label="Waiting for live source",
                 limitations=state["access"]["reasons"])
    interrupt({"type": "live_wait", "label": "Waiting for live source", "source_id": state["source_id"]})
    return {"notes": ["Live source connected; re-checking access"]}


def live_node(state: VideoState, config) -> VideoState:
    _progress(config, 0.1, "Processing live stream", "live")
    _set_project(state["project_id"], label="Processing live stream")
    session_id = state["access"]["acquire"]["live_session_id"]
    outcome = await_children(config, "live", [ChildSpec("live", "live.process", {"live_session_id": session_id,
                                                                                 "source_id": state["source_id"]},
                                                        queue="media", label="Watching live stream")],
                             label="Processing live stream")["live"]
    if not outcome.ok:
        _fail(state, (outcome.error or {}).get("message", "Live processing failed"), code="live")
    access = dict(state["access"])
    access["state"] = rs.REPLAY_ACCESSIBLE
    return {"access": access, "acquired": outcome.result or {}, "notes": ["Live stream ended; processing the recording"]}


def finalize_node(state: VideoState, config) -> VideoState:
    attempts = state.get("published") or []
    succeeded = [p for p in attempts if p["status"] == "completed"]
    with session_scope() as db:
        project = db.get(Project, uuid.UUID(state["project_id"]))
        clip_count = len(db.execute(select(Clip.id).where(Clip.project_id == project.id)).all())
        if state.get("outcome") == "access_limited":
            pass  # status/label already explain the access limitation
        elif state.get("outcome") == "transcript_only":
            project.status, project.status_label = "completed", "Completed (transcript only)"
        elif not attempts:
            project.status, project.status_label = "ready", "Clips ready"
        elif all(p["status"] == "scheduled" for p in attempts):
            project.status, project.status_label = "scheduled", "Publishing scheduled"
        elif len(succeeded) == len(attempts):
            project.status, project.status_label = "published", "Published"
        else:
            project.status = "partially_published"
            project.status_label = f"Published {len(succeeded)}, {len(attempts) - len(succeeded)} failed"
    return {"outcome": state.get("outcome") or "completed",
            "notes": [f"{clip_count} clip(s); {len(succeeded)}/{len(attempts)} publication(s) succeeded"]}


_graph = None


def graph():
    global _graph
    if _graph is None:
        g = StateGraph(VideoState)
        for name, fn in (("resolve", resolve_node), ("acquire", acquire_node), ("prepare", prepare_node),
                         ("understand", transcribe_analyze_node), ("select", select_node), ("render", render_node),
                         ("package", package_node), ("publish_gate", publish_gate_node), ("publish", publish_node),
                         ("transcript_moments", transcript_moments_node), ("report_access", report_access_node),
                         ("await_live", await_live_node), ("live", live_node), ("finalize", finalize_node)):
            g.add_node(name, fn)
        g.add_edge(START, "resolve")
        g.add_conditional_edges("resolve", route_access, {"acquire": "acquire", "live": "live", "await_live": "await_live",
                                                          "report_access": "report_access"})
        g.add_conditional_edges("acquire", route_after_acquire, {"prepare": "prepare", "transcript_moments": "transcript_moments"})
        g.add_edge("await_live", "resolve")
        g.add_edge("live", "prepare")
        g.add_edge("prepare", "understand")
        g.add_edge("understand", "select")
        g.add_edge("select", "render")
        g.add_edge("render", "package")
        g.add_edge("package", "publish_gate")
        g.add_edge("publish_gate", "publish")
        g.add_edge("publish", "finalize")
        g.add_edge("transcript_moments", "finalize")
        g.add_edge("report_access", "finalize")
        g.add_edge("finalize", END)
        _graph = g.compile(checkpointer=get_checkpointer())
    return _graph


@handler("workflow.video", queue="default", needs_secrets=True)
def video_workflow(ctx: JobContext) -> dict:
    values = run_graph_job(ctx, graph(), {"project_id": ctx.payload["project_id"], "source_id": ctx.payload["source_id"],
                                          "notes": []})
    access = values.get("access") or {}
    label = {"access_limited": "Access required", "transcript_only": "Completed (transcript only)"}.get(values.get("outcome"),
                                                                                                       "Completed")
    if values.get("outcome") == "access_limited" and access.get("state") in (rs.SOURCE_UNAVAILABLE, rs.SOURCE_PRIVATE,
                                                                             rs.UNSUPPORTED_SOURCE):
        label = "Source unavailable"
    return {"_label": label, "outcome": values.get("outcome"), "access_state": access.get("state"),
            "clips": len(values.get("clip_ids") or []), "packages": len(values.get("packages") or []),
            "published": values.get("published") or [], "notes": values.get("notes") or []}
