"""The central Brain — conversational entry point (Part 7).

LangGraph graph per conversation (thread ``chat:<conversation_id>``,
checkpointed in PostgreSQL): understand -> recall -> act -> respond.

* understand: local deterministic parser; a model is consulted only when the
  parser is unsure (and only if one is connected and within budget).
* recall:     Mem0 memories + current settings + active work.
* act:        performs the plan through tools (projects, jobs, approvals,
              settings, memory, knowledge) under the caller's permissions.
* respond:    concise execution summary; operational replies are templated
              (instant, zero cost), open questions go through the knowledge
              engine. Hidden reasoning is never exposed.
"""

from __future__ import annotations

import logging
import operator
import uuid
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..db.base import session_scope, utcnow
from ..db.models import (
    AIUsage,
    Clip,
    Conversation,
    Job,
    LearningRecord,
    Message,
    MetricSnapshot,
    OutputPackage,
    Project,
    Publication,
    Source,
    Strategy,
)
from ..ingestion.urls import detect
from ..jobs import queue
from ..providers.base import ChatMessage, NoProviderAvailable
from ..providers.hub import TaskSpec, hub
from ..security.untrusted import GUARD_INSTRUCTION
from ..services import app_settings, auth, control
from . import intents
from .runtime import get_checkpointer, thread_config

log = logging.getLogger(__name__)

MAX_TURNS = 12


def _keep_recent(existing: list[dict] | None, new: list[dict] | None) -> list[dict]:
    return ((existing or []) + (new or []))[-MAX_TURNS:]


class ChatState(TypedDict, total=False):
    conversation_id: str
    user_id: str
    user_role: str
    text: str
    attachments: list[str]
    parsed: dict
    memories: list[dict]
    context: dict
    plan: list[str]
    actions: Annotated[list[dict], operator.add]
    cards: Annotated[list[dict], operator.add]
    facts: dict
    reply: str
    turns: Annotated[list[dict], _keep_recent]


class LLMIntent(BaseModel):
    intent: Literal["process_source", "publish", "approve", "status", "analytics", "learning", "remember",
                    "forget", "costs", "connect", "help", "question", "chat", "need_source"]
    count: int | None = Field(None, ge=1, le=30)
    platforms: list[Literal["youtube", "instagram"]] = Field(default_factory=list)
    publish: bool = False
    focus: str | None = None


# --------------------------------------------------------------------- nodes
def understand(state: ChatState) -> ChatState:
    parsed = intents.parse(state["text"], has_attachments=bool(state.get("attachments")))
    data = parsed.to_dict()
    data["understood_by"] = "local_parser"
    if parsed.confidence < 0.6 and parsed.intent in ("chat",):
        try:
            refined, result = hub.complete_json(
                TaskSpec("intent", quality=1, temperature=0.0, max_tokens=200, cache_ttl_s=7 * 24 * 3600),
                [ChatMessage("system", "Classify the user's request for a video-production assistant. "
                                       "Return JSON only. " + GUARD_INSTRUCTION),
                 ChatMessage("user", state["text"][:2000])],
                LLMIntent,
            )
            if refined.intent not in ("process_source", "need_source") or not parsed.urls:
                data.update({k: v for k, v in refined.model_dump().items() if v not in (None, [], False)})
                data["understood_by"] = f"model:{result.model}"
        except NoProviderAvailable:
            pass
    return {"parsed": data}


def recall(state: ChatState) -> ChatState:
    memories: list[dict] = []
    try:
        from ..memory.service import get_memory

        memories = [
            {"memory": m["memory"], "similarity": m.get("similarity")}
            for m in get_memory().recall(state["text"], user_id=state["user_id"], limit=4, min_score=0.45)
        ]
    except Exception as exc:  # memory is helpful, never blocking
        log.warning("Memory recall failed: %s", exc)
    with session_scope() as db:
        clips = app_settings.get_section(db, "clips")
        publishing = app_settings.get_section(db, "publishing")
        active = db.execute(
            select(Job).where(Job.status.in_(queue.ACTIVE), Job.parent_id.is_(None)).order_by(Job.created_at.desc()).limit(10)
        ).scalars().all()
        strategy = db.execute(select(Strategy).where(Strategy.status == "active").order_by(Strategy.version.desc())).scalars().first()
        context = {
            "clips": clips.model_dump(),
            "publishing_policy": publishing.policy,
            "active_jobs": [queue.job_summary(j) for j in active],
            "strategy": {"version": strategy.version, "name": strategy.name} if strategy else None,
            "paused": control.is_paused(db),
        }
    return {"memories": memories, "context": context}


def act(state: ChatState) -> ChatState:
    intent = state["parsed"]["intent"]
    handler = _ACTIONS.get(intent, _act_conversation)
    try:
        return handler(state)
    except PermissionError as exc:
        return {"facts": {"error": str(exc)}, "actions": [{"step": "Not permitted", "detail": str(exc)}]}


def respond(state: ChatState) -> ChatState:
    intent = state["parsed"]["intent"]
    facts = state.get("facts") or {}
    if facts.get("error"):
        reply = facts["error"]
    else:
        reply = _REPLIES.get(intent, lambda s: s.get("facts", {}).get("answer", ""))(state)
    turns = [{"role": "user", "text": state["text"][:500]}, {"role": "assistant", "text": reply[:500]}]
    return {"reply": reply, "turns": turns}


# ------------------------------------------------------------------- helpers
def _require(state: ChatState, role: str) -> None:
    if not auth.role_at_least(state.get("user_role", "viewer"), role):
        raise PermissionError(f"Your account role ({state.get('user_role')}) cannot do that. Ask the owner for {role} access.")


def _request_params(parsed: dict, clips) -> dict[str, Any]:
    platforms = parsed.get("platforms") or []
    aspects = parsed.get("aspects") or list(clips.aspects)
    if platforms and not parsed.get("aspects"):
        aspects = ["9:16"]
    return {
        "count": parsed.get("count") or clips.default_count,
        "platforms": platforms or ["youtube", "instagram"],
        "aspects": aspects,
        "min_seconds": parsed.get("min_seconds") or clips.min_seconds,
        "max_seconds": parsed.get("max_seconds") or clips.max_seconds,
        "publish_requested": bool(parsed.get("publish")),
        "focus": parsed.get("focus"),
        "caption_style": parsed.get("caption_style") or clips.caption_style,
        "burn_captions": clips.burn_captions if parsed.get("burn_captions") is None else parsed["burn_captions"],
        "languages": parsed.get("languages") or clips.languages,
        "live_hint": bool(parsed.get("live_hint")),
    }


# -------------------------------------------------------------- act handlers
def _act_process(state: ChatState) -> ChatState:
    _require(state, "editor")
    parsed = state["parsed"]
    actions: list[dict] = []
    cards: list[dict] = []
    started: list[dict] = []
    problems: list[dict] = []
    user_id = uuid.UUID(state["user_id"])
    with session_scope() as db:
        clips = app_settings.get_section(db, "clips")
        params = _request_params(parsed, clips)
        sources: list[tuple[str, dict]] = []
        for url in parsed.get("urls", [])[:5]:
            detected = detect(url)
            if not detected.valid:
                problems.append({"url": url, "error": detected.error, "hint": detected.hint})
                actions.append({"step": "Checked link", "detail": f"{detected.error}"})
                continue
            sources.append(("url", detected.to_dict()))
        for asset_id in state.get("attachments", [])[:5]:
            sources.append(("upload", {"asset_id": asset_id}))
        for kind, info in sources:
            if kind == "url":
                source = db.execute(select(Source).where(Source.platform == info["platform"],
                                                         Source.external_id == info["external_id"])).scalar_one_or_none()
                reused = source is not None
                if source is None:
                    source = Source(original_url=info["original_url"], normalized_url=info["normalized_url"],
                                    platform=info["platform"], source_type=info["source_type"],
                                    external_id=info["external_id"], owner_user_id=user_id)
                    db.add(source)
                    db.flush()
                title = f"{info['platform'].title()} {info['source_type']} {info['external_id'][:11]}"
            else:
                from ..db.models import MediaAsset

                asset = db.get(MediaAsset, uuid.UUID(info["asset_id"]))
                if asset is None:
                    problems.append({"url": "upload", "error": "Uploaded file not found"})
                    continue
                source = db.execute(select(Source).where(Source.platform == "upload",
                                                         Source.external_id == (asset.sha256 or str(asset.id)))).scalar_one_or_none()
                reused = source is not None
                if source is None:
                    source = Source(platform="upload", source_type="file", external_id=asset.sha256 or str(asset.id),
                                    owner_user_id=user_id, media_asset_id=asset.id, media_status="available",
                                    access_status="FULL_MEDIA_ACCESS", content_sha256=asset.sha256,
                                    source_metadata={"filename": asset.info.get("filename")})
                    db.add(source)
                    db.flush()
                    asset.source_id = source.id
                title = asset.info.get("filename") or "Uploaded video"
            project = Project(user_id=user_id, source_id=source.id, conversation_id=uuid.UUID(state["conversation_id"]),
                              title=source.source_metadata.get("title") or title, instruction=state["text"][:4000],
                              request=params, status="queued", status_label="Checking link")
            db.add(project)
            db.flush()
            job = queue.enqueue(db, "workflow.video", {"project_id": str(project.id), "source_id": str(source.id)},
                                project_id=project.id, user_id=user_id, dedupe_key=f"project:{project.id}",
                                status_label="Checking link", max_attempts=4)
            started.append({"project_id": str(project.id), "job_id": str(job.id), "title": project.title,
                            "platform": source.platform, "reused_source": reused})
            cards.append({"type": "job", "job_id": str(job.id), "project_id": str(project.id), "title": project.title})
            actions.append({"step": "Created project", "detail": project.title + (" (known source — cached analysis will be reused)" if reused else "")})
        if started:
            actions.append({"step": "Started production", "detail": f"{params['count']} clip(s), {', '.join(params['aspects'])}, for {', '.join(params['platforms'])}"})
        paused = control.is_paused(db)
    return {"actions": actions, "cards": cards,
            "facts": {"started": started, "problems": problems, "params": params, "paused": paused}}


def _act_status(state: ChatState) -> ChatState:
    with session_scope() as db:
        active = db.execute(select(Job).where(Job.status.in_(queue.ACTIVE), Job.parent_id.is_(None))
                            .order_by(Job.created_at.desc()).limit(8)).scalars().all()
        recent = db.execute(select(Project).order_by(Project.updated_at.desc()).limit(5)).scalars().all()
        facts = {
            "active": [{"title": (db.get(Project, j.project_id).title if j.project_id else j.type), "label": j.status_label,
                        "progress": j.progress, "status": j.status} for j in active],
            "recent": [{"title": p.title, "status": p.status_label} for p in recent],
        }
    cards = [{"type": "job", "job_id": str(j.id), "project_id": str(j.project_id) if j.project_id else None} for j in active[:3]]
    return {"facts": facts, "cards": cards, "actions": [{"step": "Checked active work", "detail": f"{len(facts['active'])} running"}]}


def _act_remember(state: ChatState) -> ChatState:
    _require(state, "editor")
    from ..memory.service import get_memory

    events = get_memory().remember(state["text"], user_id=state["user_id"], category="preference")
    suggestion: dict[str, Any] = intents.extract_preferences(state["text"])
    cards = [{"type": "settings_suggestion", "section": "clips", "patch": suggestion}] if suggestion else []
    stored = [e.get("memory") for e in events if e.get("event") in ("ADD", "UPDATE")]
    return {"facts": {"stored": stored, "suggestion": suggestion}, "cards": cards,
            "actions": [{"step": "Saved to permanent memory", "detail": "; ".join(s[:80] for s in stored) or "no change"}]}


def _act_forget(state: ChatState) -> ChatState:
    _require(state, "editor")
    from ..memory.service import get_memory

    query = state["text"].lower().replace("forget", "").strip() or state["text"]
    matches = get_memory().recall(query, user_id=state["user_id"], limit=5, min_score=0.3)
    cards = [{"type": "memory_matches", "items": [{"id": m["id"], "memory": m["memory"]} for m in matches]}]
    return {"facts": {"matches": len(matches)}, "cards": cards,
            "actions": [{"step": "Searched memory", "detail": f"{len(matches)} possible match(es) — confirm deletion below"}]}


def _latest_project_with_packages(db) -> Project | None:
    return db.execute(
        select(Project)
        .join(Clip, Clip.project_id == Project.id)
        .join(OutputPackage, OutputPackage.clip_id == Clip.id)
        .where(OutputPackage.status.in_(("ready", "approved", "failed")))
        .order_by(Project.updated_at.desc()).limit(1)
    ).scalars().first()


def _act_publish(state: ChatState, *, approve_only: bool = False) -> ChatState:
    _require(state, "editor")
    parsed = state["parsed"]
    with session_scope() as db:
        policy = app_settings.get_section(db, "publishing").policy
        if policy == "draft_only":
            return {"facts": {"policy": policy}, "actions": [{"step": "Checked publishing policy", "detail": "draft only"}]}
        waiting = db.execute(select(Job).where(Job.type == "workflow.video", Job.status == "waiting")
                             .order_by(Job.updated_at.desc())).scalars().all()
        waiting = [j for j in waiting if (j.checkpoint or {}).get("waiting", {}).get("type") == "approval"]
        if waiting:
            job = waiting[0]
            info = job.checkpoint["waiting"]
            packages = info.get("packages", [])
            chosen = [p for p in packages if not parsed.get("clip_refs") or p.get("rank") in parsed["clip_refs"]]
            if parsed.get("platforms"):
                chosen = [p for p in chosen if p.get("platform") in parsed["platforms"]]
            queue.resume_with(db, job.id, {"approved": [p["package_id"] for p in chosen], "by": state["user_id"]},
                              label="Publishing approved clips")
            return {"facts": {"approved": len(chosen), "project_id": str(job.project_id)},
                    "cards": [{"type": "job", "job_id": str(job.id), "project_id": str(job.project_id)}],
                    "actions": [{"step": "Approved publishing", "detail": f"{len(chosen)} package(s)"}]}
        if approve_only:
            return {"facts": {"approved": 0, "nothing_waiting": True}, "actions": [{"step": "Checked approvals", "detail": "none pending"}]}
        project = _latest_project_with_packages(db)
        if project is None:
            return {"facts": {"no_packages": True}, "actions": [{"step": "Looked for ready clips", "detail": "none ready"}]}
        rows = db.execute(select(OutputPackage, Clip).join(Clip, Clip.id == OutputPackage.clip_id)
                          .where(Clip.project_id == project.id, OutputPackage.status.in_(("ready", "approved", "failed")))
                          ).all()
        chosen = [(pkg, clip) for pkg, clip in rows
                  if (not parsed.get("clip_refs") or clip.rank in parsed["clip_refs"])
                  and (not parsed.get("platforms") or pkg.platform in parsed["platforms"])]
        jobs = []
        for pkg, _clip in chosen:
            job = queue.enqueue(db, "workflow.publish", {"package_id": str(pkg.id), "approved_by": state["user_id"]},
                                project_id=project.id, user_id=uuid.UUID(state["user_id"]),
                                dedupe_key=f"publish:{pkg.id}", status_label="Preparing upload")
            jobs.append(job)
        return {"facts": {"published_jobs": len(jobs), "project_title": project.title},
                "cards": [{"type": "job", "job_id": str(j.id), "project_id": str(project.id)} for j in jobs[:4]],
                "actions": [{"step": "Queued publishing", "detail": f"{len(jobs)} package(s) from {project.title}"}]}


def _act_approve(state: ChatState) -> ChatState:
    return _act_publish(state, approve_only=True)


def _act_control(state: ChatState) -> ChatState:
    _require(state, "admin")
    action = state["parsed"]["control_action"]
    with session_scope() as db:
        result = control.apply(db, action, user_id=uuid.UUID(state["user_id"]))
    return {"facts": {"control": action, "cancelled": result.get("cancelled_jobs", 0)},
            "actions": [{"step": "Applied control", "detail": action.replace("_", " ")}]}


def _act_costs(state: ChatState) -> ChatState:
    from ..providers.hub import ModelHub

    with session_scope() as db:
        day, month = ModelHub._period_starts()
        ai = app_settings.get_section(db, "ai")
        cached = db.execute(select(func.count()).where(AIUsage.cached.is_(True), AIUsage.created_at >= month)).scalar_one()
        calls = db.execute(select(func.count()).where(AIUsage.created_at >= month, AIUsage.cached.is_(False))).scalar_one()
        facts = {"today": ModelHub.spend(db, since=day), "month": ModelHub.spend(db, since=month),
                 "daily_budget": ai.daily_budget_usd, "monthly_budget": ai.monthly_budget_usd,
                 "cached": cached, "calls": calls, "local_first": ai.local_first}
    return {"facts": facts, "actions": [{"step": "Read cost ledger", "detail": f"${facts['month']:.4f} this month"}]}


def _act_analytics(state: ChatState) -> ChatState:
    with session_scope() as db:
        published = db.execute(select(func.count()).select_from(Publication).where(Publication.status == "published")).scalar_one()
        snapshots = db.execute(select(func.count()).select_from(MetricSnapshot)).scalar_one()
        facts: dict[str, Any] = {"published": published, "snapshots": snapshots}
        if published:
            from ..analytics.compare import summary

            facts["summary"] = summary(db, platform=(state["parsed"].get("platforms") or [None])[0])
    return {"facts": facts, "cards": [{"type": "link", "href": "/learning", "label": "Open Learning Center"}],
            "actions": [{"step": "Read analytics", "detail": f"{published} published video(s), {snapshots} metric snapshot(s)"}]}


def _act_learning(state: ChatState) -> ChatState:
    with session_scope() as db:
        records = db.execute(select(LearningRecord).where(LearningRecord.status == "active")
                             .order_by(LearningRecord.created_at.desc()).limit(5)).scalars().all()
        strategy = db.execute(select(Strategy).where(Strategy.status == "active").order_by(Strategy.version.desc())).scalars().first()
        facts = {"records": [{"finding": r.finding, "confidence": r.confidence, "platform": r.platform,
                              "causal": r.causal_claim} for r in records],
                 "strategy": {"version": strategy.version, "name": strategy.name} if strategy else None}
    return {"facts": facts, "cards": [{"type": "link", "href": "/learning", "label": "Open Learning Center"}],
            "actions": [{"step": "Reviewed learning records", "detail": f"{len(facts['records'])} active finding(s)"}]}


def _act_conversation(state: ChatState) -> ChatState:
    intent = state["parsed"]["intent"]
    if intent in ("greeting", "help", "need_source", "connect"):
        return {"facts": {}}
    question = state["text"]
    if intent == "question":
        try:
            from ..knowledge.engine import ask

            result = ask(question)
            return {"facts": {"answer": result.answer, "knowledge": result.as_dict()},
                    "actions": [{"step": "Answered from " + ("stored knowledge" if result.reused else ", ".join(result.providers)),
                                 "detail": f"verification: {result.verification_status}"}]}
        except NoProviderAvailable:
            return {"facts": {"no_model": True}}
    memories = "\n".join(f"- {m['memory']}" for m in state.get("memories", []))
    history = "\n".join(f"{t['role']}: {t['text']}" for t in (state.get("turns") or [])[-6:])
    try:
        result = hub.complete(
            TaskSpec("chat_reply", quality=1, temperature=0.4, max_tokens=400),
            [ChatMessage("system", "You are CLIPFORGE, a concise assistant that turns long videos into Shorts and Reels "
                                   "and publishes them. Be brief and practical. Known owner preferences:\n" + (memories or "- none") +
                                   "\n" + GUARD_INSTRUCTION),
             ChatMessage("user", (f"Recent conversation:\n{history}\n\n" if history else "") + question)],
        )
        return {"facts": {"answer": result.text.strip(), "model": result.model},
                "actions": [{"step": "Replied", "detail": f"via {result.connection_name}"}]}
    except NoProviderAvailable:
        return {"facts": {"no_model": True}}


_ACTIONS = {
    "process_source": _act_process,
    "status": _act_status,
    "remember": _act_remember,
    "forget": _act_forget,
    "publish": _act_publish,
    "approve": _act_approve,
    "control": _act_control,
    "costs": _act_costs,
    "analytics": _act_analytics,
    "learning": _act_learning,
}


# ------------------------------------------------------------------ replies
def _reply_process(state: ChatState) -> str:
    facts = state["facts"]
    lines: list[str] = []
    started = facts.get("started", [])
    params = facts.get("params", {})
    if started:
        n = len(started)
        lines.append(
            f"On it. I've started {'a project' if n == 1 else f'{n} projects'} and I'm checking the link and what access is "
            f"available. Target: {params.get('count')} clip(s) in {', '.join(params.get('aspects', []))} for "
            f"{' and '.join(p.title() for p in params.get('platforms', []))}"
            + (f", focused on “{params['focus']}”" if params.get("focus") else "") + "."
        )
        if any(s["reused_source"] for s in started):
            lines.append("I've seen this source before, so I'll reuse its saved transcript and analysis where valid.")
        if params.get("publish_requested"):
            lines.append("You asked me to publish — I'll prepare the packages and follow your publishing policy before anything goes live.")
        if facts.get("paused"):
            lines.append("Note: automation is currently paused, so this will wait until you resume it.")
        lines.append("I'll only promise what the source access allows: if just metadata or a transcript is available I'll say so clearly.")
    for problem in facts.get("problems", []):
        lines.append(f"⚠️ {problem['error']}" + (f" {problem['hint']}" if problem.get("hint") else ""))
    return "\n\n".join(lines) or "I couldn't find a link I can work with. Paste a YouTube or Instagram link (or upload a file)."


def _reply_status(state: ChatState) -> str:
    facts = state["facts"]
    if not facts["active"]:
        recent = facts["recent"]
        if not recent:
            return "Nothing is running right now. Paste a video link to get started."
        return "Nothing is running right now. Recent projects:\n" + "\n".join(f"• {p['title']} — {p['status']}" for p in recent)
    return "Here's what I'm working on:\n" + "\n".join(
        f"• {a['title']} — {a['label']} ({round(a['progress'] * 100)}%)" for a in facts["active"])


def _reply_remember(state: ChatState) -> str:
    facts = state["facts"]
    text = "Got it — saved to permanent memory. I'll apply this to future work." if facts["stored"] else "I already had that noted."
    if facts.get("suggestion"):
        text += " I can also make it a default setting — use the button below to apply it."
    return text


def _reply_publish(state: ChatState) -> str:
    facts = state["facts"]
    if facts.get("policy") == "draft_only":
        return "Publishing is set to draft-only, so I won't upload anything. Change the publishing policy in Settings if you want me to publish."
    if facts.get("nothing_waiting"):
        return "There's nothing waiting for approval right now."
    if facts.get("no_packages"):
        return "I don't have finished clips ready to publish yet. Send me a video link first, or check My Videos."
    if "approved" in facts:
        return f"Approved {facts['approved']} package(s). I'm publishing them now and will confirm only after each platform confirms."
    return (f"Queued {facts['published_jobs']} package(s) from “{facts['project_title']}” for publishing. "
            "I'll report each one as published only after the platform API confirms it.")


def _reply_control(state: ChatState) -> str:
    facts = state["facts"]
    return {
        "emergency_stop": f"Emergency stop is ON. I cancelled {facts['cancelled']} active job(s) and blocked external AI calls and publishing. Clear it in Settings when ready.",
        "pause": "All automation is paused. Running steps will stop at their next safe point.",
        "resume": "Automation resumed.",
    }.get(facts["control"], "Done.")


def _reply_costs(state: ChatState) -> str:
    f = state["facts"]
    return (f"AI spend: ${f['today']:.4f} today (budget ${f['daily_budget']:.2f}), ${f['month']:.4f} this month "
            f"(budget ${f['monthly_budget']:.2f}). {f['calls']} paid/local model call(s) and {f['cached']} answered from cache this month. "
            + ("Local-first mode is on: free local tools and models are always tried first." if f["local_first"] else ""))


def _reply_analytics(state: ChatState) -> str:
    f = state["facts"]
    if not f["published"]:
        return "No published videos yet, so there's no performance data. Once clips are published I'll collect the metrics the platforms make available."
    summary = f.get("summary") or {}
    return summary.get("text") or f"{f['published']} published video(s) with {f['snapshots']} metric snapshot(s). Open the Learning Center for comparisons."


def _reply_learning(state: ChatState) -> str:
    f = state["facts"]
    if not f["records"]:
        return ("I haven't recorded any lessons yet — learning needs published videos with enough analytics to compare. "
                "Until then I use my default strategy" + (f" (v{f['strategy']['version']})." if f["strategy"] else "."))
    lines = ["Current findings (correlations unless marked as tested):"]
    for r in f["records"]:
        lines.append(f"• {r['finding']} — confidence {r['confidence']:.0%}{' (tested)' if r['causal'] else ''}")
    return "\n".join(lines)


def _reply_conversation(state: ChatState) -> str:
    intent = state["parsed"]["intent"]
    f = state.get("facts") or {}
    if intent == "greeting":
        return "Hi! Paste a YouTube or Instagram link and tell me what you want — for example “make 5 Shorts from this”."
    if intent in ("help",):
        return ("Send me a link and an instruction — “create 5 Shorts from this video”, “process this Reel”, or “clip this livestream”. "
                "I check what access is available, transcribe, find the strongest moments, edit vertical clips with captions, "
                "write titles and descriptions, and publish to YouTube and Instagram under your publishing policy. "
                "You can also ask about status, costs, analytics and what I've learned.")
    if intent == "need_source":
        return ("Sure — which video? Paste a YouTube video or livestream link, an Instagram Reel or video post link, a Google Drive "
                "file link, or a direct link to a media file you have rights to. You can also upload a file.")
    if intent == "connect":
        return "You can connect AI models in AI Connections and YouTube/Instagram/Google Drive in Settings → Connections. I'll walk you through any step that needs your login."
    if f.get("no_model"):
        return ("I don't have an AI model connected to answer open questions right now. Everything in the video pipeline still works "
                "locally. Connect a provider in AI Connections — Ollama gives you free local models.")
    answer = f.get("answer", "")
    knowledge = f.get("knowledge")
    if knowledge:
        status = knowledge["verification_status"].replace("_", " ")
        answer += f"\n\n— Source: {', '.join(knowledge['providers'])}{' (from memory)' if knowledge['reused'] else ''}; status: {status}."
        if knowledge.get("disagreements"):
            answer += " Note: the models disagreed on some points."
    return answer or "I'm not sure how to help with that yet."


_REPLIES = {
    "process_source": _reply_process,
    "status": _reply_status,
    "remember": _reply_remember,
    "forget": lambda s: "Here are the memories that look related. Choose which to delete." if s["facts"]["matches"] else "I couldn't find a matching memory.",
    "publish": _reply_publish,
    "approve": _reply_publish,
    "control": _reply_control,
    "costs": _reply_costs,
    "analytics": _reply_analytics,
    "learning": _reply_learning,
}
for _name in ("greeting", "help", "need_source", "connect", "question", "chat"):
    _REPLIES[_name] = _reply_conversation


# --------------------------------------------------------------------- graph
_graph = None


def get_graph():
    global _graph
    if _graph is None:
        builder = StateGraph(ChatState)
        builder.add_node("understand", understand)
        builder.add_node("recall", recall)
        builder.add_node("act", act)
        builder.add_node("respond", respond)
        builder.add_edge(START, "understand")
        builder.add_edge("understand", "recall")
        builder.add_edge("recall", "act")
        builder.add_edge("act", "respond")
        builder.add_edge("respond", END)
        _graph = builder.compile(checkpointer=get_checkpointer())
    return _graph


def handle_message(conversation_id: uuid.UUID, user, text: str, attachments: list[str] | None = None) -> dict[str, Any]:
    """Persist the user message, run the Brain, persist and return the reply."""
    text = (text or "").strip()[:8000]
    with session_scope() as db:
        conversation = db.get(Conversation, conversation_id)
        if conversation is None or conversation.user_id != user.id:
            raise LookupError("Conversation not found")
        db.add(Message(conversation_id=conversation_id, role="user", content=text,
                       meta={"attachments": attachments or []}))
        if conversation.title == "New conversation" and text:
            conversation.title = text[:60] + ("…" if len(text) > 60 else "")
        conversation.updated_at = utcnow()
    state = get_graph().invoke(
        {"conversation_id": str(conversation_id), "user_id": str(user.id), "user_role": user.role, "text": text,
         "attachments": attachments or [], "actions": [], "cards": [], "facts": {}, "plan": []},
        thread_config(f"chat:{conversation_id}"),
    )
    meta = {"intent": state["parsed"]["intent"], "understood_by": state["parsed"].get("understood_by"),
            "actions": state.get("actions", []), "cards": state.get("cards", []),
            "memories_used": len(state.get("memories", []))}
    with session_scope() as db:
        message = Message(conversation_id=conversation_id, role="assistant", content=state["reply"], meta=meta)
        db.add(message)
        db.flush()
        message_id = message.id
    return {"id": str(message_id), "role": "assistant", "content": state["reply"], "meta": meta}

