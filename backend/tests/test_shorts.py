"""P0 Shorts stages: first-3-seconds hook (score, runway removal, first line, frame-one text,
tension-first) and loop seam (end adjustment, bridge), the review hold in the publish gate, and
"everything off by default"."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
from sqlalchemy import select

from clipforge.db.base import session_scope
from clipforge.db.models import Clip, OutputPackage
from clipforge.handlers.media_jobs import reorder_keep, shorts_stage
from clipforge.media import render
from clipforge.services import app_settings
from clipforge.shorts import hook as shook
from clipforge.shorts import loop as sloop


def words_for(script: str, *, start: float = 0.0, word_s: float = 0.32, gap: float = 0.06,
              pauses: dict[int, float] | None = None) -> list[dict]:
    """Word timestamps for ``script``; ``pauses`` adds silence before word index n."""
    out, t = [], start
    for n, text in enumerate(script.split()):
        t += (pauses or {}).get(n, 0.0)
        out.append({"text": text, "start": round(t, 3), "end": round(t + word_s, 3), "conf": 1.0})
        t += word_s + gap
    return out


WEAK = ("Hey guys, welcome back to the channel. So um today we're going to talk about pricing. "
        "The biggest mistake freelancers make is charging by the hour. Clients don't buy hours, they buy outcomes. "
        "If you price the outcome, you can earn three times more for the same work. That's the mistake to fix first.")


def test_weak_opening_is_held_with_reasons_when_not_rebuilt():
    words = words_for(WEAK)
    result = shook.reconstruct(words, 0.0, words[-1]["end"] + 0.1, has_video=False, rebuild=False)
    assert result.status == "held"
    assert result.after.score < shook.HookConfig().threshold
    assert any("greeting" in r for r in result.before.reasons)
    assert result.keep == [(0.0, words[-1]["end"] + 0.1)]  # nothing changed


def test_runway_is_removed_and_the_first_line_lands_fast():
    words = words_for(WEAK)
    end = words[-1]["end"] + 0.1
    result = shook.reconstruct(words, 0.0, end, has_video=False, cfg=shook.HookConfig(min_duration_s=8))
    biggest = next(w for w in words if w["text"] == "The" and w["start"] > 3)
    assert abs(result.start - (biggest["start"] - 0.15)) < 0.2 or abs(result.start - biggest["start"]) < 0.2
    assert result.after.first_word_s is not None and result.after.first_word_s <= 1.5
    assert any("runway" in c for c in result.changes)
    assert result.status == "improved" and result.after.score > result.before.score


def test_leading_silence_is_trimmed_to_land_the_first_line():
    words = words_for("Why do most people fail at pricing? They copy competitors instead of pricing value. "
                      "Price the value and the problem goes away.", start=3.0)
    result = shook.reconstruct(words, 0.0, words[-1]["end"] + 0.1, has_video=False, cfg=shook.HookConfig(min_duration_s=5))
    assert abs(result.start - (3.0 - 0.15)) < 0.01
    assert result.after.first_word_s <= 0.2


def test_frame_one_text_comes_only_from_the_clip():
    words = words_for(WEAK)
    text = shook.make_text_hook(words, 0.0, words[-1]["end"])
    assert text and 1 <= len(text.split()) <= 5 and text == text.upper()
    assert shook.grounded(text, words)
    assert "MISTAKE" in text or "BIGGEST" in text
    assert not shook.grounded("You will get rich overnight", words)  # invented claims are refused


def test_text_hook_is_drawn_on_frame_one_in_the_middle_third(tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x406080:size=1280x720:rate=25:duration=4",
                    "-f", "lavfi", "-i", "sine=frequency=220:duration=4", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(src)], check=True, timeout=120)

    def frame0(hook_text):
        plan = render.plan_edits(0.0, 4.0, [], [], remove_silences=False, aspect="9:16", burn_captions=False,
                                 punch_in=False, fade_in=False, hook_text=hook_text)
        out = tmp_path / f"out_{bool(hook_text)}.mp4"
        render.render(src, plan, [], out, tmp_path / f"work_{bool(hook_text)}", has_video=True, has_audio=True)
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-frames:v", "1", "-vf", "scale=270:480",
                              "-f", "rawvideo", "-pix_fmt", "gray", "-"], check=True, capture_output=True).stdout
        return np.frombuffer(raw, dtype=np.uint8).reshape(480, 270).astype(np.float32)

    plain, hooked = frame0(None), frame0("BIGGEST PRICING MISTAKE")
    assert plain.mean() > 40  # frame one is the picture, not a fade from black
    diff = np.abs(hooked - plain)
    middle, top, bottom = diff[160:320].mean(), diff[:150].mean(), diff[330:].mean()
    assert middle > 5 * max(top, bottom, 0.5), (middle, top, bottom)


def test_tension_first_only_when_switched_on():
    script = ("The answer is to raise your prices because clients judge value by price. "
              "So why do cheap freelancers lose clients? They signal low quality. "
              "That is why the cheapest offer rarely wins the best clients in any market.")
    words = words_for(script)
    end = words[-1]["end"] + 0.1
    off = shook.reconstruct(words, 0.0, end, has_video=False, cfg=shook.HookConfig(min_duration_s=5, threshold=0.99))
    assert len(off.keep) == 1
    on = shook.reconstruct(words, 0.0, end, has_video=False,
                           cfg=shook.HookConfig(min_duration_s=5, threshold=0.99, tension_first=True))
    assert len(on.keep) >= 2
    question = next(w for w in words if w["text"] == "why")
    assert on.keep[0][0] <= question["start"] <= on.keep[0][1]
    assert any("own question" in c for c in on.changes)
    # The render keeps every interval, just in the new order.
    keep = [(0.0, 5.0), (5.5, end)]
    reordered = reorder_keep(keep, on.keep)
    assert abs(sum(b - a for a, b in reordered) - sum(b - a for a, b in keep)) < 0.2
    assert reordered[0][0] == on.keep[0][0]


def test_loop_end_moves_from_mid_sentence_to_a_sentence_end():
    script = ("The mistake is pricing by the hour. Clients pay for outcomes, not time. "
              "Price the outcome and you will earn more. That is the mistake to stop making today.")
    words = words_for(script)
    mid = next(w for w in words if w["text"] == "and")["end"] + 0.02  # cut right after "and"
    cfg = sloop.LoopConfig(min_duration_s=3, search_s=5.0)
    before = sloop.seam_score(words, 0.0, mid, cfg=cfg)
    plan = sloop.choose_end(words, 0.0, mid, cfg=cfg)
    assert plan.end != mid and plan.assessment.score > before.score
    ending = [w for w in words if w["end"] <= plan.end][-1]["text"]
    assert ending.endswith(".")
    assert plan.assessment.parts["echo"] > 0  # "mistake" opens and closes the clip


def test_bridge_when_no_cut_loops(tmp_path):
    words = words_for("and then we went and and so we kept going and and", word_s=0.3)
    plan = sloop.choose_end(words, 0.0, words[-1]["end"], cfg=sloop.LoopConfig(min_duration_s=1))
    assert 0.2 <= plan.bridge_s <= 0.5 and any("blend" in c for c in plan.changes)
    src = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=540x960:rate=30:duration=3",
                    "-f", "lavfi", "-i", "sine=frequency=330:duration=3", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(src)], check=True, timeout=120)
    out = sloop.bridge(src, tmp_path / "looped.mp4", plan.bridge_s, 3.0)
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(out)],
                                      check=True, capture_output=True).stdout)
    assert abs(float(probe["format"]["duration"]) - 3.0) < 0.15
    assert sloop.frame_similarity(out) is not None


def test_everything_is_off_by_default_and_changes_nothing():
    with session_scope() as db:
        cfg = app_settings.get_section(db, "shorts")
    assert not (cfg.hook_scorer or cfg.hook_reconstruct or cfg.hook_gate or cfg.loop_seam or cfg.tension_first)
    words = words_for(WEAK)
    out = shorts_stage(words, {}, 0.0, 20.0, has_video=False, scores={}, cfg=cfg)
    assert (out["start"], out["end"], out["plan_options"], out["record"]) == (0.0, 20.0, {}, {})


def test_publish_gate_holds_a_weak_opening(tmp_path):
    import pytest

    from clipforge.handlers.video_workflow import publish_gate_node
    from tests.test_publishing import _make_package

    package_id = _make_package(tmp_path, "youtube")
    with session_scope() as db:
        app_settings.update_section(db, "publishing", {"policy": "auto_publish"})
        app_settings.update_section(db, "shorts", {"hook_scorer": True, "hook_gate": True})
        package = db.get(OutputPackage, package_id)
        clip_id = package.clip_id
    state = {"project_id": None, "access": {"method": "upload", "owned": True},
             "packages": [{"package_id": str(package_id), "platform": "youtube", "rank": 1, "qc_passed": True,
                           "review_words": 0, "clip_id": str(clip_id), "hook_score": 0.31, "hook_held": True,
                           "hook_reasons": ["opens with a greeting or filler"]}]}
    with session_scope() as db:
        state["project_id"] = str(db.get(Clip, clip_id).project_id)
    # Not approved automatically: it goes to you for approval (outside a running workflow that pause raises).
    with pytest.raises(Exception):  # noqa: B017 - langgraph's interrupt outside a graph run
        publish_gate_node(state, {"configurable": {"thread_id": "x"}})
    with session_scope() as db:
        gate = db.get(Clip, clip_id).shorts["publish_gate_result"]
    assert gate["result"] == "held"
    assert any("weak first 3 seconds" in r and "greeting" in r for r in gate["reasons"])
    # With the gate off the same clip is approved automatically again.
    with session_scope() as db:
        app_settings.update_section(db, "shorts", {"hook_gate": False})
    assert publish_gate_node(state, {"configurable": {"thread_id": "x"}})["approved"] == [str(package_id)]
    with session_scope() as db:
        assert db.execute(select(Clip).where(Clip.id == clip_id)).scalar_one().shorts["publish_gate_result"]["result"] == "approved"


def test_settings_roundtrip(tmp_path):
    with session_scope() as db:
        app_settings.update_section(db, "shorts", {"hook_threshold": 0.6, "loop_seam": True})
        cfg = app_settings.get_section(db, "shorts")
    assert cfg.hook_threshold == 0.6 and cfg.loop_seam and not cfg.hook_scorer
    assert Path  # keep the import used in helpers above


def test_text_hook_keeps_every_word_in_at_most_three_lines():
    for text in ("BIGGEST MISTAKE SMALL BUSINESSES MAKE", "WHY", "STOP UNDERPRICING YOUR CONSULTING SERVICES TODAY"):
        lines = render.wrap_hook(text)
        assert 1 <= len(lines) <= 3 and " ".join(lines).split() == text.split()


def test_text_hook_goes_where_the_face_is_not():
    face_high = [{"t": 0.0, "faces": [{"cx": 0.5, "cy": 0.32, "area": 0.05}]}]
    face_low = [{"t": 0.2, "faces": [{"cx": 0.5, "cy": 0.7, "area": 0.05}]}]
    assert 0.55 <= shook.text_position(face_high, 0.0) <= 0.64  # lower part of the middle third
    assert 0.34 <= shook.text_position(face_low, 0.0) <= 0.45
    assert shook.text_position([], 0.0) == 0.42
