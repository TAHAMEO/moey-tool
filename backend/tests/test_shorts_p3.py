"""P3: adaptive length, alternate openings for weak clips, A/B tests across clips, and cost per
clip / per published Short (from the Model Hub's metered usage)."""

from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace

from sqlalchemy import select

from clipforge.analytics import costs
from clipforge.db.base import session_scope, utcnow
from clipforge.db.models import ABTest, AIUsage, Clip, Job, MetricSnapshot, OutputPackage, Publication
from clipforge.handlers.media_jobs import shorts_stage
from clipforge.handlers.video_workflow import choose_variant
from clipforge.services import app_settings
from clipforge.shorts import abtest
from clipforge.shorts import hook as shook
from clipforge.shorts import length as slength
from tests.test_shorts import WEAK, words_for

TIP = ("Here's how to write a cold email that gets replies. First, keep it under five sentences. "
       "Second, mention one thing you noticed about their business. Third, ask one simple question. "
       "That's the whole trick to getting replies. Most people write a whole page about themselves. "
       "Nobody reads that. They skim the first line and delete it. So the first line has to be about them. "
       "If you remember one thing, make the first line about the reader and keep it short.")
STORY = ("When I started my agency I was charging fifty dollars an hour. I remember the day a client asked me "
         "for a fixed price instead. I was scared, so I quoted three thousand dollars. Then I spent one week on it. "
         "One day later the client signed. That was the day I stopped selling hours.")


def test_content_type_and_targets():
    assert slength.content_type(TIP) == "list"
    assert slength.content_type(STORY) == "story"
    tip = slength.target(TIP, ["youtube"])
    story = slength.target(STORY, ["youtube", "instagram"])
    assert tip.prefer_max_s < story.prefer_max_s and story.max_s <= 58
    assert slength.target("ok.", ["youtube"]).content_type == "general"
    nudged = slength.target(STORY, ["youtube"], learned_band="20-35 s")
    assert nudged.prefer_max_s <= 35 and any("kept viewers best" in r for r in nudged.reasons)


def test_adaptive_length_ends_a_tip_at_its_conclusion_without_a_blend():
    words = words_for(TIP, word_s=0.38, gap=0.07)
    end = words[-1]["end"] + 0.1
    with session_scope() as db:
        app_settings.update_section(db, "shorts", {"adaptive_length": True})
        cfg = app_settings.get_section(db, "shorts")
    out = shorts_stage(words, {}, 0.0, end, has_video=False, scores={}, cfg=cfg, platforms=["youtube"])
    plan = out["record"]["length_plan"]
    assert plan["content_type"] == "list"
    concluded = next(w for w in words if w["text"] == "replies." and w["start"] > 10)
    assert abs(out["end"] - concluded["end"]) < 0.3 and out["end"] < end - 10  # ends at "That's the whole trick…"
    assert out["record"]["loop"]["bridge_s"] == 0 and "fade_out" not in out["plan_options"]


def test_alternate_openings_for_a_weak_clip():
    script = ("Um so yeah. Okay. Hello. Right so anyway. Hmm, okay. "
              "The biggest mistake freelancers make is charging by the hour. Clients buy outcomes, not hours. "
              "If you price the outcome, you can earn three times more for the same work. That is the mistake to fix.")
    words = words_for(script, word_s=0.4, gap=0.1)
    end = words[-1]["end"] + 0.1
    options = shook.alternates(words, 0.0, end, has_video=False, cfg=shook.HookConfig(min_duration_s=8))
    assert options and options[0]["kind"] == "later" and options[0]["delta"] > 0
    assert "biggest mistake" in options[0]["first_line"]
    # Used automatically when switched on and the opening is otherwise held.
    with session_scope() as db:
        app_settings.update_section(db, "shorts", {"hook_scorer": True, "hook_reconstruct": False, "alternate_hooks": True,
                                                   "alternate_hooks_apply": True, "min_s": 8})
        cfg = app_settings.get_section(db, "shorts")
    out = shorts_stage(words, {}, 0.0, end, has_video=False, scores={}, cfg=cfg)
    hook = out["record"]["hook"]
    assert hook["status"] == "improved" and hook["alternate_used"]["kind"] == "later"
    assert out["start"] == hook["alternate_used"]["start"] and out["reorder"] is None  # nothing reordered
    assert out["record"]["alternate_hooks"]


def test_ab_assignment_balances_arms_and_keeps_them(tmp_path):
    from tests.test_publishing import _make_package

    clip_ids = []
    with session_scope() as db:
        for _ in range(4):
            pid = _make_package(tmp_path, "youtube")
            clip_ids.append(db.get(OutputPackage, pid).clip_id)
    arms = []
    for cid in clip_ids:
        with session_scope() as db:
            ab = abtest.assign(db, cid, "title", 5)
            clip = db.get(Clip, cid)
            clip.shorts = {**(clip.shorts or {}), "ab": ab}
            arms.append(ab["arm"])
    assert sorted(arms) == ["A", "A", "B", "B"]
    with session_scope() as db:
        again = abtest.assign(db, clip_ids[0], "title", 5)
        assert again["arm"] == arms[0]  # a re-render keeps the arm
        abtest.assign(db, clip_ids[0], "hook", 5)  # switching the test stops the old one
        statuses = {t.dimension: t.status for t in db.execute(select(ABTest)).scalars()}
    assert statuses == {"title": "stopped", "hook": "running"}


def test_ab_arms_change_what_is_made():
    words = words_for(WEAK)
    end = words[-1]["end"] + 0.1
    with session_scope() as db:
        app_settings.update_section(db, "shorts", {"packaging_variants": True})
        cfg = app_settings.get_section(db, "shorts")
    a = shorts_stage(words, {}, 0.0, end, has_video=False, scores={}, cfg=cfg, ab_arm={"dimension": "first_frame", "arm": "A"})
    b = shorts_stage(words, {}, 0.0, end, has_video=False, scores={}, cfg=cfg, ab_arm={"dimension": "first_frame", "arm": "B"})
    assert a["plan_options"]["hook_text"] == a["record"]["first_frame_texts"][1 - 1]
    assert b["plan_options"]["hook_text"] == a["record"]["first_frame_texts"][1] != a["plan_options"]["hook_text"]
    no_text = shorts_stage(words, {}, 0.0, end, has_video=False, scores={}, cfg=cfg, ab_arm={"dimension": "hook", "arm": "B"})
    assert "hook_text" not in no_text["plan_options"] and no_text["record"]["text_hook_removed_for_test"]
    variants = [SimpleNamespace(title="one"), SimpleNamespace(title="two")]
    assert choose_variant(SimpleNamespace(shorts={"ab": {"dimension": "title", "arm": "B"}}), variants).title == "two"
    assert choose_variant(SimpleNamespace(shorts={"ab": {"dimension": "title", "arm": "A"}}), variants).title == "one"
    assert choose_variant(SimpleNamespace(shorts={}), variants).title == "one"


def test_ab_test_concludes_only_with_enough_clips(tmp_path):
    from tests.test_publishing import _make_package

    with session_scope() as db:
        test = abtest.current(db, "first_frame", 5)
        test_id = str(test.id)
    arms = [("A", 0.5), ("A", 0.52), ("A", 0.48), ("A", 0.55), ("A", 0.47),
            ("B", 0.71), ("B", 0.74), ("B", 0.69), ("B", 0.66), ("B", 0.72)]
    for n, (arm, viewed) in enumerate(arms):
        pid = _make_package(tmp_path, "youtube")
        with session_scope() as db:
            clip = db.get(Clip, db.get(OutputPackage, pid).clip_id)
            clip.shorts = {"ab": {"test_id": test_id, "dimension": "first_frame", "arm": arm},
                           "analytics": {"youtube": {"signals": {"viewed_rate": viewed}}}}
        if n == 5:
            with session_scope() as db:
                early = abtest.evaluate(db)[0]
            assert early["status"] == "running" and early["measured"] == {"A": 5, "B": 1}
    with session_scope() as db:
        result = abtest.evaluate(db)[0]
    assert result["status"] == "concluded" and result["winner"] == "B" and result["metric"] == "viewed_rate"
    assert "second first-frame text kept viewers better" in result["conclusion"] and result["p_value"] < 0.1


def test_costs_per_clip_and_per_published_short(tmp_path):
    from tests.test_publishing import _make_package

    pid = _make_package(tmp_path, "youtube")
    with session_scope() as db:
        package = db.get(OutputPackage, pid)
        clip = db.get(Clip, package.clip_id)
        project_id = clip.project_id
        second = Clip(project_id=project_id, source_id=clip.source_id, rank=2, start_s=0, end_s=5, title="b", hook="b",
                      transcript_text="b", status="rendered")
        db.add(second)
        now = utcnow()
        render_job = Job(type="media.render", payload={"clip_id": str(clip.id)}, project_id=project_id, status="completed",
                         started_at=now - timedelta(seconds=40), finished_at=now)
        workflow = Job(type="video.workflow", payload={}, project_id=project_id, status="completed",
                       started_at=now - timedelta(seconds=100), finished_at=now)
        db.add_all([render_job, workflow])
        db.flush()
        db.add_all([
            AIUsage(provider_type="openai", model="m", task="metadata", cost_usd=0.004, job_id=render_job.id, project_id=project_id),
            AIUsage(provider_type="openai", model="m", task="select", cost_usd=0.002, job_id=workflow.id, project_id=project_id,
                    cost_estimated=True),
            AIUsage(provider_type="ollama", model="local", task="metadata", cost_usd=0.0, job_id=workflow.id, project_id=project_id),
        ])
        pub = Publication(package_id=package.id, platform="youtube", idempotency_key=uuid.uuid4().hex, status="published",
                          external_id="VIDcost0001", published_at=now - timedelta(hours=30))
        db.add(pub)
        db.flush()
        db.add(MetricSnapshot(publication_id=pub.id, platform="youtube", metrics={"views": 2000}, unavailable={}, source="t"))
        second_id = second.id
        first_id = clip.id
    with session_scope() as db:
        report = costs.project_costs(db, project_id)
        channel = costs.channel_costs(db)
    first, other = report["clips"][str(first_id)], report["clips"][str(second_id)]
    assert first["ai_usd"] == 0.004 + 0.001 and other["ai_usd"] == 0.001  # own + half of the shared call
    assert first["processing_s"] == 40 + 50 and other["processing_s"] == 50
    assert first["estimated"] and report["total"]["ai_usd"] == 0.006 and report["total"]["ai_calls"] == 3
    short = next(s for s in channel["shorts"] if s["title"].startswith("Never compete"))
    assert short["ai_usd"] == 0.005 and short["usd_per_1k_views"] == 0.0025 and short["views"] == 2000


def test_p3_is_off_by_default():
    with session_scope() as db:
        cfg = app_settings.get_section(db, "shorts")
    assert not (cfg.adaptive_length or cfg.alternate_hooks or cfg.alternate_hooks_apply) and cfg.ab_test == "off"
    words = words_for(TIP)
    out = shorts_stage(words, {}, 0.0, 30.0, has_video=False, scores={}, cfg=cfg, platforms=["youtube"])
    assert (out["plan_options"], out["record"]) == ({}, {})
