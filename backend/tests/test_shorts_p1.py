"""P1 Shorts stages: re-hook at ~20-25 s, kinetic captions with power words, the clip's
transcript export, and ranked packaging variants (title / first-frame text / description /
hashtags). Everything is off by default and nothing is invented."""

from __future__ import annotations

import json
import subprocess
import uuid

import numpy as np
import pysubs2

from clipforge.db.base import session_scope
from clipforge.db.models import Clip, MediaAsset, OutputPackage
from clipforge.handlers.media_jobs import apply_rehook, export_transcript, shorts_stage
from clipforge.handlers.video_workflow import _packaging_variants, apply_variant
from clipforge.media import metadata, render
from clipforge.services import app_settings
from clipforge.shorts import captions as scaptions
from clipforge.shorts import packaging as spack
from clipforge.shorts import retention as sret
from clipforge.storage import get_storage
from clipforge.vendor.autoclip import captions as vcaptions
from clipforge.vendor.autoclip.transcript import Word
from tests.test_shorts import WEAK, words_for

# ~33 s: a point concludes around 21-22 s ("That's why…"), then a new point starts.
LONG = ("Most freelancers set their price by the hour. They look at what other people charge and pick a number. "
        "Clients compare that number with ten other offers. The cheapest offer wins and everyone loses money. "
        "That's why hourly pricing keeps you broke. "
        "Now here is the one change that tripled my income. Charge for the result the client wants, not your time. "
        "A website that brings ten new customers is worth far more than forty hours. "
        "Price the result and the hours stop mattering.")


def test_rehook_lands_where_a_thought_resolves_between_20_and_25_s():
    words = words_for(LONG, word_s=0.42, gap=0.08)
    duration = words[-1]["end"] + 0.1
    plan = sret.plan_rehook(words, duration, seed="clip-1")
    assert plan.at is not None and 15 <= plan.at <= 30, plan.to_dict()
    concluded = next(w for w in words if w["text"] == "broke.")
    assert abs(plan.at - concluded["end"]) < 0.3  # right after "That's why hourly pricing keeps you broke."
    assert any("concluded" in r for r in plan.exit.reasons)
    assert plan.zoom and plan.zoom[0] == plan.at and plan.zoom[1] - plan.zoom[0] >= 1.0
    # The text comes before the exit point and is either the clip's own next line or a framing line.
    assert plan.text_start < plan.at
    if plan.text_kind == "own_words":
        assert all(t in LONG.upper() for t in plan.text.replace("?", "").split())
    else:
        assert plan.text in sret.FRAMINGS
    assert "re-hook at" in plan.changes[0]


def test_rehook_never_mid_sentence_and_not_in_short_clips():
    words = words_for(LONG, word_s=0.42, gap=0.08)
    short = sret.plan_rehook(words[:40], words[39]["end"])
    assert short.at is None and "re-hook from" in short.skipped
    for point in sret.exit_points(words, words[-1]["end"]):
        before = [w for w in words if w["end"] <= point.t + 1e-6][-1]
        assert before["text"][-1] in ".!?"


def test_rehook_uses_framing_when_the_next_line_is_not_a_hook():
    script = ("We moved the office in March because the rent went up again. The new place is closer to the station. "
              "It took two weekends to pack everything into boxes. That's why the team was tired in April. "
              "The kitchen is bigger and there is a small garden outside. We also bought new chairs for the meeting room.")
    words = words_for(script, word_s=0.45, gap=0.08)
    plan = sret.plan_rehook(words, words[-1]["end"] + 0.1, seed="a")
    assert plan.at is not None and plan.text_kind == "framing" and plan.text in sret.FRAMINGS
    off = sret.plan_rehook(words, words[-1]["end"] + 0.1, cfg=sret.RehookConfig(framing=False))
    assert off.text is None and off.zoom  # zoom only


def test_rehook_zoom_and_text_are_rendered(tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=8",
                    "-f", "lavfi", "-i", "sine=frequency=220:duration=8", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(src)], check=True, timeout=120)

    def frame(plan, t):
        out = tmp_path / f"out_{len(plan.zoom_windows)}{len(plan.overlays)}.mp4"
        render.render(src, plan, [], out, tmp_path / "work", has_video=True, has_audio=True)
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(out), "-frames:v", "1", "-vf", "scale=270:480",
                              "-f", "rawvideo", "-pix_fmt", "gray", "-"], check=True, capture_output=True).stdout
        probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(out)],
                                          check=True, capture_output=True).stdout)
        return np.frombuffer(raw, dtype=np.uint8).reshape(480, 270).astype(np.float32), float(probe["format"]["duration"])

    base = {"remove_silences": False, "aspect": "9:16", "burn_captions": False, "punch_in": False}
    plain, d0 = frame(render.plan_edits(0.0, 8.0, [], [], **base), 5.0)
    plan = render.plan_edits(0.0, 8.0, [], [], **base)
    plan.zoom_windows.append([4.5, 6.0])
    zoomed, d1 = frame(plan, 5.0)
    before, _ = frame(plan, 3.0)
    plain_before, _ = frame(render.plan_edits(0.0, 8.0, [], [], **base), 3.0)
    assert np.abs(zoomed - plain).mean() > 8  # zoomed inside the window
    assert np.abs(before - plain_before).mean() < 3  # untouched outside it
    assert abs(d1 - d0) < 0.1
    plan2 = render.plan_edits(0.0, 8.0, [], [], **base)
    plan2.overlays.append({"text": "NOW THE IMPORTANT PART", "start": 4.0, "seconds": 2.0, "y": 0.42})
    texted, _ = frame(plan2, 5.0)
    diff = np.abs(texted - plain)
    assert diff[160:330].mean() > 4 * max(diff[:140].mean(), diff[360:].mean(), 0.5)


def test_apply_rehook_records_a_retention_plan():
    words = words_for(LONG, word_s=0.42, gap=0.08)
    end = words[-1]["end"] + 0.1
    plan = render.plan_edits(0.0, end, [], words, remove_silences=False)
    with session_scope() as db:
        cfg = app_settings.get_section(db, "shorts")
    record = apply_rehook(plan, words, cfg, visual=[], seed="x")
    assert record["at"] and plan.zoom_windows == [record["zoom"]]
    assert plan.overlays and plan.overlays[0]["text"] == record["text"]
    json.dumps(record)  # stored on the clip as JSON


def test_power_words_highlight_numbers_keywords_and_rare_words():
    scores = scaptions.power_scores(LONG, idf=scaptions.source_idf(words_for(LONG + " " + WEAK)))
    assert scores["ten"] == 1.0 and scores["tripled"] >= 0.7
    assert scores["the"] == 0 and scores["and"] == 0
    for line, expect in ((["THE", "CHEAPEST", "OFFER"], {"CHEAPEST", "OFFER"}), (["TEN", "OTHER", "OFFERS."], {"TEN"}),
                         (["and", "the", "so"], set())):
        picked = {line[i] for i in scaptions.pick(line, scores)}
        assert picked <= expect | {"OFFER"} and (bool(picked) == bool(expect))
        assert len(picked) <= 3
    four = ["CHARGE", "FOR", "THE", "RESULT"]
    assert 1 <= len(scaptions.pick(four, scores)) <= 2


def test_kinetic_captions_colour_only_the_power_words():
    words = [Word(text=t, start=i * 0.4, end=i * 0.4 + 0.35) for i, t in enumerate(["it", "tripled", "my", "income", "in", "ten", "weeks"])]
    scores = scaptions.power_scores("it tripled my income in ten weeks")
    style = vcaptions.get_style("kinetic")
    subs = vcaptions.build_ass(words, style, width=1080, height=1920, emphasis=scaptions.emphasis(scores))
    assert style.max_words == 3 and subs.styles[vcaptions.STYLE_NAME].fontsize >= 130  # large, phrase by phrase
    accent = vcaptions.ass_colour_override(style.accent)
    highlighted = set()
    for event in subs.events:
        for chunk in event.text.split("{\\r}"):
            if accent in chunk:
                highlighted.add(chunk.rsplit("}", 1)[-1].strip().split()[-1])
    assert "TEN" in highlighted and "TRIPLED" in highlighted
    assert not highlighted & {"IT", "MY", "IN"}
    assert any("\\t(0,90" in e.text for e in subs.events)  # the spoken power word pops


def test_kinetic_style_renders_and_reports_power_words(tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x406080:size=1280x720:rate=25:duration=3",
                    "-f", "lavfi", "-i", "sine=frequency=220:duration=3", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(src)], check=True, timeout=120)
    words = words_for("It tripled my income in ten weeks.", word_s=0.3)
    plan = render.plan_edits(0.0, 3.0, [], words, remove_silences=False, aspect="9:16", caption_style="kinetic")
    decisions = render.render(src, plan, words, tmp_path / "k.mp4", tmp_path / "w", has_video=True, has_audio=True)
    assert decisions["captions_burned"] and {"tripled", "ten"} & {w.lower().strip(".") for w in decisions["power_words"]}


def test_packaging_variants_are_ranked_grounded_and_sized():
    variants = spack.build(WEAK, llm_title="Get Rich Overnight With This Trick", channel="Moey")
    assert 2 <= len(variants) <= 3
    assert [v.rank for v in variants] == list(range(1, len(variants) + 1))
    assert [v.score for v in variants] == sorted((v.score for v in variants), reverse=True)
    best = variants[0]
    assert 30 <= len(best.title) <= 50 and best.title.lower().startswith("pricing")
    for v in variants:
        assert spack.grounded_title(v.title, WEAK)  # no words the speaker didn't say (bar framing words)
        assert "rich" not in v.title.lower()  # the invented model title was dropped
        assert v.first_frame_text and 1 <= len(v.first_frame_text.split()) <= 2 and v.first_frame_text.isupper()
        assert v.hashtags["youtube"][0] == "#shorts" and v.hashtags["instagram"][-1] == "#reels"
        assert "#Pricing" in v.hashtags["youtube"]
        assert "charging by the hour" in v.youtube_description  # keyword-rich excerpt of what was said
        assert "Hey guys" not in v.youtube_description
        assert "Follow Moey" in v.instagram_caption
    assert len({v.title for v in variants}) == len(variants)


def test_first_frame_text_is_one_or_two_words_from_the_clip():
    texts = spack.first_frame_texts(WEAK)
    assert texts[0] == "BIGGEST MISTAKE"
    assert all(1 <= len(t.split()) <= 2 for t in texts)


def test_package_gets_the_best_variant_and_the_rest_are_kept(tmp_path):
    from tests.test_publishing import _make_package

    package_id = _make_package(tmp_path, "youtube")
    with session_scope() as db:
        cfg = app_settings.get_section(db, "shorts")
        package = db.get(OutputPackage, package_id)
        clip = db.get(Clip, package.clip_id)
        clip.transcript_text = WEAK
        clip.shorts = {"first_frame_texts": ["BIGGEST MISTAKE", "PRICING"], "first_frame_text": "BIGGEST MISTAKE"}
        copy = metadata.local_copy(WEAK, "The biggest mistake freelancers make is charging by the hour.")
        variants = _packaging_variants(clip, copy, cfg, "Moey", credit="")
        apply_variant(package, variants[0].to_dict())
        assert package.title == variants[0].title + " #shorts"
        assert variants[0].burned and variants[0].first_frame_text == "BIGGEST MISTAKE"
        stored = clip.shorts["packaging_variants"]
        assert len(stored) == len(variants) and stored[0]["title"] == variants[0].title
        ig = OutputPackage(clip_id=clip.id, platform="instagram")
        apply_variant(ig, stored[1])
        assert ig.caption == stored[1]["instagram_caption"] and ig.hashtags[-1] == "#reels"


def test_transcript_is_exported_and_kept(tmp_path):
    from tests.test_publishing import _make_package

    package_id = _make_package(tmp_path, "youtube")
    with session_scope() as db:
        project_id = db.get(Clip, db.get(OutputPackage, package_id).clip_id).project_id
    words = words_for(WEAK)
    key = f"projects/{project_id}/clips/{uuid.uuid4()}/clip01_transcript.txt"
    record = export_transcript(words, key, project_id=project_id)
    text = get_storage().local_path(key).read_text(encoding="utf-8")
    assert "The biggest mistake freelancers make is charging by the hour." in text.splitlines()
    assert record["keyword"] == "pricing" and record["words"] == len(words)
    with session_scope() as db:
        asset = db.get(MediaAsset, uuid.UUID(record["transcript_asset_id"]))
        assert asset.kind == "transcript" and asset.mime == "text/plain"


def test_p1_is_off_by_default_and_changes_nothing():
    with session_scope() as db:
        cfg = app_settings.get_section(db, "shorts")
    assert not (cfg.rehook or cfg.kinetic_captions or cfg.seo_transcript or cfg.packaging_variants)
    words = words_for(LONG)
    out = shorts_stage(words, {}, 0.0, 30.0, has_video=False, scores={}, cfg=cfg)
    assert (out["plan_options"], out["record"]) == ({}, {})


def test_kinetic_and_packaging_switches_set_render_options():
    with session_scope() as db:
        app_settings.update_section(db, "shorts", {"kinetic_captions": True, "packaging_variants": True})
        cfg = app_settings.get_section(db, "shorts")
    words = words_for(WEAK)
    out = shorts_stage(words, {}, 0.0, words[-1]["end"] + 0.1, has_video=False, scores={}, cfg=cfg)
    assert out["plan_options"]["caption_power"]["mistake"] > 0.5
    assert out["plan_options"]["hook_text"] == out["record"]["first_frame_text"] == "BIGGEST MISTAKE"
    assert out["plan_options"]["fade_in"] is False


def test_ass_file_with_overlay_stays_above_the_captions(tmp_path):
    ass = tmp_path / "c.ass"
    render.add_overlay_text(ass, "BIGGEST MISTAKE SMALL BUSINESSES MAKE", 0, 1.5, width=1080, height=1920,
                            accent="#FFE500", y_ratio=0.6)
    event = pysubs2.load(str(ass)).events[0]
    y = int(event.text.split("\\pos(")[1].split(",")[1].split(")")[0])
    assert 0.36 * 1920 <= y <= 0.6 * 1920
