"""The Brain's assistant: improves clips and controls CLIPFORGE from plain language.

"Make clip 2 five seconds shorter and use karaoke captions", "give clip 1 a punchier title",
"how can I make these clips better?", "pause everything".

A language model — a local one on this PC (Ollama, LM Studio…) when connected, free and private —
reads the request with the current project's clips (scores, length, captions, quality checks) and
returns a *plan*: a reply plus steps, validated against :class:`Plan`. It never acts itself: each
step is checked here and carried out with the caller's permissions through the same code as the
buttons in the app (re-render, title edits, settings, controls). Without any model, simple
requests (clip numbers, seconds, caption styles, quoted titles) still work from a local parser.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from ..db.base import session_scope
from ..db.models import Clip, Message, OutputPackage, Project, Render
from ..providers.base import ChatMessage, NoProviderAvailable
from ..providers.hub import TaskSpec, hub
from ..security.untrusted import GUARD_INSTRUCTION, fence
from ..services import app_settings, auth, clip_edits, control
from .intents import clip_refs, wants_edit  # noqa: F401 - re-exported

log = logging.getLogger(__name__)

CaptionStyle = Literal["bold_pop", "karaoke_fill", "clean_lower", "boxed", "kinetic"]
DEFAULTS = ("default_count", "min_seconds", "max_seconds", "caption_style", "remove_silences", "punch_in_zoom",
            "burn_captions")
STYLE_NAMES = {"bold_pop": "bold pop", "karaoke_fill": "karaoke", "clean_lower": "clean lower-third", "boxed": "boxed",
               "kinetic": "kinetic"}


class Step(BaseModel):
    action: Literal["edit_clip", "set_title", "rerender", "change_default", "control"]
    clip: int | None = Field(None, ge=0, le=50, description="clip number; 0 = every clip")
    trim_start_s: float | None = Field(None, ge=-60, le=120, description="seconds cut from the start (negative: start earlier)")
    trim_end_s: float | None = Field(None, ge=-60, le=120, description="seconds cut from the end (negative: end later)")
    caption_style: CaptionStyle | None = None
    burn_captions: bool | None = None
    title: str | None = Field(None, max_length=150)
    platform: Literal["youtube", "instagram", "all"] | None = None
    setting: Literal["default_count", "min_seconds", "max_seconds", "caption_style", "remove_silences",
                     "punch_in_zoom", "burn_captions"] | None = None
    value: str | float | bool | None = None
    control: Literal["pause", "resume", "hold_publishing", "resume_publishing"] | None = None


class Plan(BaseModel):
    reply: str = Field("", max_length=3000)
    steps: list[Step] = Field(default_factory=list, max_length=10)

    @field_validator("steps", mode="before")
    @classmethod
    def _drop_unusable(cls, value: Any) -> Any:
        # Small models sometimes add an empty or unknown step: keep the usable ones.
        if not isinstance(value, list):
            return []
        keep = []
        for item in value:
            try:
                keep.append(Step.model_validate(item))
            except ValueError:
                continue
        return keep


SYSTEM = """You are the assistant inside CLIPFORGE, an app on the owner's PC that turns long videos into \
vertical Shorts and Reels (clips) and publishes them. You help the owner improve their clips and you control the app.

Answer with JSON: {"reply": "...", "steps": [...]}. "reply" is what you tell the owner: brief, friendly, \
specific, in the owner's language. "steps" are the changes to make; use [] when only answering or advising.

Possible steps:
- {"action": "edit_clip", "clip": N, "trim_start_s": S, "trim_end_s": E, "caption_style": STYLE, "burn_captions": true/false} \
— change clip N (0 = every clip) and render it again. trim_start_s cuts S seconds from the start (negative starts earlier), \
trim_end_s cuts E seconds from the end (negative ends later). STYLE is one of bold_pop, karaoke_fill, clean_lower, boxed, \
kinetic. Leave out what does not change.
- {"action": "set_title", "clip": N, "title": "...", "platform": "youtube"|"instagram"|"all"} — a new title (max 100 characters).
- {"action": "rerender", "clip": N} — render clip N again as it is.
- {"action": "change_default", "setting": NAME, "value": V} — suggest a default for future videos (the owner confirms). \
NAME is one of default_count, min_seconds, max_seconds, caption_style, remove_silences, punch_in_zoom, burn_captions.
- {"action": "control", "control": "pause"|"resume"|"hold_publishing"|"resume_publishing"} — pause or resume all work, or only publishing.

Rules: only make changes the owner asked for. For advice, look at the clip data: scores go from 0 to 1 and the lowest \
ones are the clip's weak points (hook = first 3 seconds, completeness, emotion, clarity, novelty, ...). Good Shorts \
are 20-45 seconds with a strong first line. Never mention clips that are not listed. If there is no project, help \
the owner start one (paste a YouTube or Instagram link, or attach a video). """ + GUARD_INSTRUCTION


# ----------------------------------------------------------------- context
def focus_project(db, conversation_id: str | None) -> Project | None:
    """The project this conversation is about: the latest one it started, else the latest overall."""
    if conversation_id:
        rows = db.execute(select(Message.meta).where(Message.conversation_id == uuid.UUID(conversation_id),
                                                     Message.role == "assistant")
                          .order_by(Message.created_at.desc()).limit(30)).scalars()
        for meta in rows:
            for card in (meta or {}).get("cards", []):
                if card.get("type") == "job" and card.get("project_id"):
                    project = db.get(Project, uuid.UUID(card["project_id"]))
                    if project is not None:
                        return project
    return db.execute(select(Project).order_by(Project.created_at.desc())).scalars().first()


def project_facts(db, project: Project | None) -> dict:
    """What the assistant (and the local fallback) know about the project's clips."""
    if project is None:
        return {"project": None, "clips": []}
    clips = []
    for clip in db.execute(select(Clip).where(Clip.project_id == project.id).order_by(Clip.rank)).scalars():
        render = db.execute(select(Render).where(Render.clip_id == clip.id, Render.status == "done")
                            .order_by(Render.created_at.desc())).scalars().first()
        scores = {k: round(float(v), 2) for k, v in (clip.scores or {}).items() if isinstance(v, int | float)}
        qc = (render.quality_report or {}) if render else {}
        decisions = (render.edit_decisions or {}) if render else {}
        packages = {p.platform: p.title for p in db.execute(select(OutputPackage).where(OutputPackage.clip_id == clip.id))
                    .scalars()}
        clips.append({
            "rank": clip.rank, "id": str(clip.id), "title": clip.title or "", "start": round(clip.start_s, 1),
            "end": round(clip.end_s, 1), "length": round(clip.end_s - clip.start_s, 1),
            "score": round(clip.total_score or 0.0, 2), "scores": scores,
            "weakest": sorted(scores.items(), key=lambda kv: kv[1])[:2],
            "hook_score": round(clip.hook_score, 2) if clip.hook_score is not None else None,
            "status": clip.status, "caption_style": decisions.get("caption_style"),
            "captions_burned": decisions.get("captions_burned"),
            "qc_failed": [c["name"] for c in qc.get("checks", []) if not c.get("ok")],
            "packages": packages, "opening": (clip.transcript_text or "")[:160],
        })
    return {"project": {"id": str(project.id), "title": project.title, "status": project.status_label or project.status},
            "clips": clips}


def facts_text(facts: dict) -> str:
    if not facts.get("project"):
        return "There is no project yet."
    title = fence("project_title", facts["project"]["title"] or "Untitled", 200)
    lines = [f"Current project: {title} ({facts['project']['status']})"]
    if not facts["clips"]:
        lines.append("It has no clips yet (still processing, or no clips were found).")
    for c in facts["clips"]:
        weak = ", ".join(f"{k} {v}" for k, v in c["weakest"]) or "n/a"
        lines.append(
            f"Clip {c['rank']}: {c['start']}-{c['end']} s ({c['length']} s), score {c['score']}, weakest: {weak}"
            + (f", hook {c['hook_score']}" if c["hook_score"] is not None else "")
            + f", captions {c['caption_style'] or 'default'}{'' if c['captions_burned'] is not False else ' (not burned in)'}"
            + (f", quality problems: {', '.join(c['qc_failed'])}" if c["qc_failed"] else "")
            + f", status {c['status']}\n  title: {fence('clip_title', c['title'] or '(none)', 150)}"
            + f"\n  opening words: {fence('transcript', c['opening'], 200)}")
    return "\n".join(lines)


# ----------------------------------------------------------- local parser
_NUMBER = r"(\d{1,3}(?:\.\d)?|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty)"
_WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
             "ten": 10, "fifteen": 15, "twenty": 20}
_SEC = r"\s*(?:s|secs?|seconds?)\b"


def _num(value: str) -> float:
    return float(_WORD_NUM.get(value.lower(), value))


def local_plan(text: str, facts: dict) -> Plan | None:
    """Plain requests without any AI model: clip numbers, seconds, caption styles, quoted titles."""
    lower = text.lower()
    refs = clip_refs(text) or ([0] if re.search(r"\b(them|these|those|all)\b", lower) else [])
    if not refs and len(facts.get("clips") or []) == 1:
        refs = [1]
    if not refs:
        return None
    steps: list[Step] = []
    edit: dict[str, Any] = {}
    if m := re.search(r"\b(?:cut|trim|remove|drop|skip)\s+(?:the\s+)?(?:first|opening|beginning)\s+" + _NUMBER + _SEC, lower):
        edit["trim_start_s"] = _num(m.group(1))
    if m := re.search(r"\bstart\s+" + _NUMBER + _SEC + r"\s+(later|earlier)", lower):
        edit["trim_start_s"] = _num(m.group(1)) * (1 if m.group(2) == "later" else -1)
    if m := re.search(r"\b(?:cut|trim|remove|drop)\s+(?:the\s+)?(?:last|final|ending|end)\s+" + _NUMBER + _SEC, lower):
        edit["trim_end_s"] = _num(m.group(1))
    if m := re.search(r"\bend\s+" + _NUMBER + _SEC + r"\s+(earlier|sooner|later)", lower):
        edit["trim_end_s"] = _num(m.group(1)) * (-1 if m.group(2) == "later" else 1)
    if m := re.search(_NUMBER + _SEC + r"\s+(shorter|longer)", lower) or \
            re.search(r"(shorter|longer)\s+by\s+" + _NUMBER + _SEC, lower):
        amount, way = (m.group(1), m.group(2)) if m.group(1)[0].isdigit() or m.group(1) in _WORD_NUM else (m.group(2), m.group(1))
        edit["trim_end_s"] = _num(amount) * (1 if way == "shorter" else -1)
    for style, pattern in (("kinetic", r"kinetic|animated"), ("karaoke_fill", r"karaoke"), ("boxed", r"\bboxed?\b"),
                           ("clean_lower", r"\bclean\b|minimal|lower[- ]third"), ("bold_pop", r"\bbold\b|\bpop\b")):
        if re.search(pattern, lower) and re.search(r"caption|subtitle", lower):
            edit["caption_style"] = style
            break
    if re.search(r"\b(no|without|remove|turn off|hide)\s+(the\s+)?(burned[- ]in\s+)?(captions|subtitles)\b", lower):
        edit["burn_captions"] = False
    elif re.search(r"\b(add|burn|show|turn on)\s+(the\s+)?(captions|subtitles)\b", lower):
        edit["burn_captions"] = True
    title = None
    if m := re.search(r"(?:rename|retitle|call|title)\b[^\"“”']*?(?:to|as|:)?\s*[\"“']([^\"”']{2,150})[\"”']", text, re.IGNORECASE):
        title = m.group(1).strip()
    for ref in refs:
        if edit:
            steps.append(Step(action="edit_clip", clip=ref, **edit))
        if title:
            steps.append(Step(action="set_title", clip=ref, title=title, platform="all"))
        if not edit and not title and re.search(r"\bre-?render|render (it|them|again)\b", lower):
            steps.append(Step(action="rerender", clip=ref))
    if not steps:
        return None
    return Plan(reply="", steps=steps)


def advice(facts: dict) -> str:
    """Data-driven suggestions without a model: the weakest scores and quality problems per clip."""
    if not facts.get("clips"):
        return ""
    hints = {"hook": "a stronger first line (start on the most surprising sentence)",
             "completeness": "a cleaner ending (finish the thought)", "emotion": "a more energetic moment",
             "clarity": "less filler and fewer pauses", "novelty": "a more surprising point",
             "pacing": "faster pacing (trim pauses)", "interest": "a topic closer to what your audience likes"}
    lines = []
    for c in facts["clips"]:
        parts = []
        for name, value in c["weakest"]:
            if value < 0.5:
                parts.append(f"{name} is weak ({value}) — try {hints.get(name, 'a different moment')}")
        if c["length"] > 50:
            parts.append(f"it is long ({c['length']} s) — Shorts of 20-45 s usually keep viewers better")
        if c["qc_failed"]:
            parts.append("quality check: " + ", ".join(c["qc_failed"]))
        if parts:
            lines.append(f"• Clip {c['rank']}: " + "; ".join(parts) + ".")
    return "\n".join(lines) or "Your clips look solid: no weak scores or quality problems stand out."


# ------------------------------------------------------------------ the model
def model_plan(text: str, facts: dict, *, memories: list[dict], turns: list[dict], project_id: str | None) -> tuple[Plan, str]:
    """Ask the connected model for a plan (local models first). Raises NoProviderAvailable."""
    memory_lines = "\n".join(f"- {m['memory']}" for m in memories) or "- none"
    history = "\n".join(f"{t['role']}: {t['text']}" for t in turns[-6:])
    user = (f"{facts_text(facts)}\n\nOwner preferences:\n{memory_lines}\n\n"
            + (f"Recent conversation:\n{history}\n\n" if history else "")
            + f"Owner's request: {text}")
    plan, result = hub.complete_json(
        TaskSpec("assistant", quality=1, temperature=0.2, max_tokens=900, timeout_s=180,
                 project_id=uuid.UUID(project_id) if project_id else None),
        [ChatMessage("system", SYSTEM), ChatMessage("user", user)], Plan)
    return plan, f"{result.connection_name} · {result.model}"


def make_plan(text: str, *, conversation_id: str | None, memories: list[dict], turns: list[dict]) -> dict:
    """The plan for one request: from the model when one is available, else from the local parser."""
    from ..providers import local_ai

    with session_scope() as db:
        project = focus_project(db, conversation_id)
        facts = project_facts(db, project)
        local_ai.auto_connect(db)  # a running Ollama is connected by itself the first time
    try:
        plan, via = model_plan(text, facts, memories=memories, turns=turns, project_id=(facts["project"] or {}).get("id"))
        return {"plan": plan, "facts": facts, "via": via}
    except NoProviderAvailable as exc:
        log.info("Assistant without a model: %s", exc)
    plan = local_plan(text, facts)
    return {"plan": plan, "facts": facts, "via": None}


# ------------------------------------------------------------------ carrying it out
def _clips_for(db, project_id: str, ref: int | None) -> list[Clip]:
    clips = db.execute(select(Clip).where(Clip.project_id == uuid.UUID(project_id)).order_by(Clip.rank)).scalars().all()
    if not clips:
        return []
    if ref in (None, 0):
        return list(clips)
    if ref == -1:
        return [clips[-1]]
    return [c for c in clips if c.rank == ref]


def execute(plan: Plan, facts: dict, *, user_id: str, role: str) -> dict:
    """Carry out the plan's steps; returns {done: [...], problems: [...], cards: [...], jobs: [...]}."""
    done: list[dict] = []
    problems: list[str] = []
    cards: list[dict] = []
    project_id = (facts.get("project") or {}).get("id")
    user = uuid.UUID(user_id)
    for step in plan.steps:
        try:
            if step.action == "control":
                if not auth.role_at_least(role, "admin"):
                    raise PermissionError("Only an admin can pause or resume CLIPFORGE.")
                with session_scope() as db:
                    if step.control in ("hold_publishing", "resume_publishing"):
                        switch = "disable_integration" if step.control == "hold_publishing" else "enable_integration"
                        for platform in ("youtube", "instagram"):
                            control.apply(db, switch, user_id=user, integration=f"publish:{platform}")
                    elif step.control:
                        control.apply(db, step.control, user_id=user)
                done.append({"step": "Applied control", "detail": (step.control or "").replace("_", " ")})
                continue
            if step.action == "change_default":
                if step.setting not in DEFAULTS or step.value is None:
                    continue
                value = _setting_value(step.setting, step.value)
                with session_scope() as db:
                    app_settings.get_section(db, "clips").model_validate(
                        {**app_settings.get_section(db, "clips").model_dump(), step.setting: value})
                cards.append({"type": "settings_suggestion", "section": "clips", "patch": {step.setting: value}})
                done.append({"step": "Suggested a default", "detail": f"{step.setting.replace('_', ' ')} → {value}"})
                continue
            if not auth.role_at_least(role, "editor"):
                raise PermissionError("Only an editor can change clips.")
            if not project_id:
                problems.append("There is no project with clips yet.")
                break
            with session_scope() as db:
                targets = _clips_for(db, project_id, step.clip)
                if not targets:
                    problems.append(f"There is no clip {step.clip} in this project.")
                    continue
                for clip in targets:
                    detail = _apply(db, clip, step, user)
                    if detail:
                        done.append({"step": f"Clip {clip.rank}", "detail": detail})
        except clip_edits.EditError as exc:
            problems.append(str(exc))
        except PermissionError as exc:
            problems.append(str(exc))
        except ValueError as exc:
            problems.append(f"Could not apply a change: {exc}"[:300])
    if any("re-render" in d["detail"] for d in done) and project_id:
        cards.append({"type": "link", "href": f"/videos/{project_id}", "label": "Open the project"})
    return {"done": done, "problems": problems, "cards": cards}


def _setting_value(setting: str, value):
    if setting in ("remove_silences", "punch_in_zoom", "burn_captions"):
        return value if isinstance(value, bool) else str(value).lower() in ("true", "yes", "on", "1")
    if setting == "caption_style":
        if value not in clip_edits.CAPTION_STYLES:
            raise ValueError(f"unknown caption style {value}")
        return value
    number = float(value)
    return int(number) if setting == "default_count" else number


def _apply(db, clip: Clip, step: Step, user: uuid.UUID) -> str | None:
    if step.action == "set_title":
        if not step.title:
            return None
        platforms = clip_edits.set_title(db, clip, step.title, platform=step.platform)
        return f"new title “{clip.title}”" + (f" ({', '.join(platforms)})" if platforms else "")
    if step.action == "rerender":
        clip_edits.edit_clip(db, clip, force_render=True, user_id=user)
        return "re-rendering"
    start = end = None
    changes = []
    if step.trim_start_s:
        start = clip.start_s + step.trim_start_s
        changes.append(f"{'cut' if step.trim_start_s > 0 else 'added'} {abs(step.trim_start_s):g} s at the start")
    if step.trim_end_s:
        end = clip.end_s - step.trim_end_s
        changes.append(f"{'cut' if step.trim_end_s > 0 else 'added'} {abs(step.trim_end_s):g} s at the end")
    if step.caption_style:
        changes.append(f"{STYLE_NAMES[step.caption_style]} captions")
    if step.burn_captions is not None:
        changes.append("captions burned in" if step.burn_captions else "no burned-in captions")
    if not changes:
        return None
    clip_edits.edit_clip(db, clip, start_s=start, end_s=end, caption_style=step.caption_style,
                         burn_captions=step.burn_captions, user_id=user)
    return ", ".join(changes) + f"; re-rendering ({clip.end_s - clip.start_s:.1f} s)"
