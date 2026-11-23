"""P2: the originality guardrail (compare with the last posts; other title option or hold) and
early-retention feedback (YouTube retention curve + engaged views, CSV/manual import, flop flags,
style ranking). Platform calls go to the local fake of the documented APIs (contract tests) —
they are not evidence that the real APIs return these numbers."""

from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from clipforge.analytics import collect as collector
from clipforge.analytics import retention
from clipforge.db.base import session_scope, utcnow
from clipforge.db.models import Clip, MetricSnapshot, OutputPackage, Publication
from clipforge.services import app_settings
from clipforge.shorts import originality
from tests.conftest import connect

# ----------------------------------------------------------------- originality


def _fp(title: str, *, hook="The biggest mistake is pricing by the hour.", template="keyword_promise", caption="kinetic",
        rehook_kind="framing", ref="x") -> originality.Fingerprint:
    clip = SimpleNamespace(hook=hook, shorts={
        "hook": {"status": "improved", "keep": [[0, 30]], "text_hook": "BIGGEST MISTAKE"},
        "retention_plan": {"text_kind": rehook_kind, "text": "NOW THE IMPORTANT PART", "zoom": [21, 22.5]},
        "loop": {"bridged": False}, "first_frame_text": "BIGGEST MISTAKE",
        "packaging_variants": [{"title": title, "template": template}], "seo": {"keyword": "pricing"}})
    package = SimpleNamespace(title=f"{title} #shorts", caption="", metadata_method=f"variant:{template}")
    return originality.fingerprint(clip, package, caption_style=caption, duration=28.0, ref=ref)


def test_hook_types():
    assert originality.hook_type("Why do clients ghost you?") == "question"
    assert originality.hook_type("So the biggest mistake is pricing by the hour.") == "warning"
    assert originality.hook_type("Here's how to price a website.") == "how_to"
    assert originality.hook_type("I lost three clients in one week.") == "story"
    assert originality.hook_type("Clients pay for outcomes.") == "statement"


def test_near_duplicate_is_flagged_and_a_fresh_title_is_not():
    recent = [_fp("Pricing: The biggest mistake freelancers make", ref="p1")]
    same = originality.check(_fp("Pricing: The biggest mistake freelancers make"), recent)
    assert same.flagged and same.max_similarity >= 0.85 and "too similar" in same.reasons[0]
    other = originality.check(_fp("Pricing: Clients pay for outcomes, not hours"), recent)
    assert not other.flagged and other.max_similarity < 0.85  # same template, different words: fine once


def test_template_streak_is_flagged():
    titles = ["Pricing: Raise your rates every year", "Clients: Stop answering emails at night",
              "Proposals: Send one page, not ten", "Invoices: Ask for half up front", "Niches: Pick one industry first"]
    recent = [_fp(t, ref=str(n)) for n, t in enumerate(titles)]
    candidate = _fp("Portfolio: Show three projects, not thirty")
    result = originality.check(candidate, recent)
    assert result.flagged and any("same template as your last 5" in r for r in result.reasons)
    assert not originality.check(candidate, recent[:4]).flagged  # 4 in a row is still fine
    assert not originality.check(_fp("Why do clients ghost you?", hook="Why do clients ghost you?",
                                     template="keyword_question"), recent).flagged


def _published_like(tmp_path, *, title: str, hours_ago: float) -> uuid.UUID:
    from tests.test_publishing import _make_package

    package_id = _make_package(tmp_path, "youtube")
    with session_scope() as db:
        package = db.get(OutputPackage, package_id)
        package.title = title
        package.status = "published"
        db.add(Publication(package_id=package.id, platform="youtube", idempotency_key=uuid.uuid4().hex, status="published",
                           external_id=uuid.uuid4().hex[:11], published_at=utcnow() - timedelta(hours=hours_ago)))
    return package_id


def _gate_state(package_id) -> dict:
    with session_scope() as db:
        package = db.get(OutputPackage, package_id)
        clip = db.get(Clip, package.clip_id)
        return {"project_id": str(clip.project_id), "access": {"method": "upload", "owned": True},
                "packages": [{"package_id": str(package_id), "platform": "youtube", "rank": 1, "qc_passed": True,
                              "review_words": 0, "clip_id": str(clip.id)}]}


def test_gate_holds_a_repeat_and_never_auto_publishes_it(tmp_path):
    from clipforge.handlers.video_workflow import publish_gate_node

    for n in range(3):
        _published_like(tmp_path, title="Never compete on price #shorts", hours_ago=10 + n)
    from tests.test_publishing import _make_package

    package_id = _make_package(tmp_path, "youtube")  # same title, same template
    with session_scope() as db:
        app_settings.update_section(db, "publishing", {"policy": "auto_publish"})
        app_settings.update_section(db, "shorts", {"originality_gate": True})
    with pytest.raises(Exception):  # noqa: B017 - langgraph's interrupt outside a graph run = waits for you
        publish_gate_node(_gate_state(package_id), {"configurable": {"thread_id": "x"}})
    with session_scope() as db:
        clip = db.get(Clip, db.get(OutputPackage, package_id).clip_id)
        flags = clip.shorts["originality_flags"]["youtube"]
        gate = clip.shorts["publish_gate_result"]
    assert flags["flagged"] and flags["compared"] == 3 and flags["matches"]
    assert gate["result"] == "held" and any("repeats your recent posts" in r for r in gate["reasons"])
    # Off: the same clip is approved automatically again.
    with session_scope() as db:
        app_settings.update_section(db, "shorts", {"originality_gate": False})
    assert publish_gate_node(_gate_state(package_id), {"configurable": {"thread_id": "x"}})["approved"] == [str(package_id)]


def test_gate_switches_to_another_title_option_when_that_breaks_the_pattern(tmp_path):
    from clipforge.handlers.video_workflow import publish_gate_node
    from tests.test_publishing import _make_package

    for n in range(2):
        _published_like(tmp_path, title="Never compete on price #shorts", hours_ago=5 + n)
    package_id = _make_package(tmp_path, "youtube")
    option = {"rank": 2, "title": "Why clients stop comparing your price", "template": "keyword_why",
              "youtube_description": "Why clients stop comparing your price\n\n#shorts", "instagram_caption": "x",
              "hashtags": {"youtube": ["#shorts"], "instagram": ["#reels"]}}
    with session_scope() as db:
        clip = db.get(Clip, db.get(OutputPackage, package_id).clip_id)
        clip.transcript_text = "Never compete on price. Clients stop comparing your price when you show value."
        clip.shorts = {"packaging_variants": [{"rank": 1, "title": "Never compete on price", "template": "keyword_promise"},
                                              option]}
        app_settings.update_section(db, "publishing", {"policy": "auto_publish"})
        app_settings.update_section(db, "shorts", {"originality_gate": True})
    result = publish_gate_node(_gate_state(package_id), {"configurable": {"thread_id": "x"}})
    assert result["approved"] == [str(package_id)]
    with session_scope() as db:
        package = db.get(OutputPackage, package_id)
        flags = db.get(Clip, package.clip_id).shorts["originality_flags"]["youtube"]
        assert package.title == "Why clients stop comparing your price #shorts"
    assert not flags["flagged"] and flags["switched_to"] == 2 and "switched to title option #2" in flags["reasons"][0]


# ----------------------------------------------------------------- retention


def test_curve_metrics_read_3s_end_and_replays():
    curve = [(i / 100, 1.05 - i / 200, 0.6) for i in range(1, 101)]  # starts above 1: replays
    m = retention.curve_metrics(curve, 30.0)
    assert m["first_3s_retention"] == pytest.approx(1.05 - 0.1 / 2, abs=0.01)
    assert m["end_retention"] == pytest.approx(0.55, abs=0.01) and m["replay_ratio"] > 1
    assert retention.curve_metrics([], 30) == {}


@pytest.mark.contract
def test_youtube_retention_curve_and_engaged_views_are_collected(platforms):
    account_id = connect("youtube", "UC_OWNER")
    platforms.uploaded_videos["VIDret"] = {"id": "VIDret", "statistics": {"viewCount": "2000"}}
    platforms.analytics_rows["VIDret"] = [1900, 400.0, 14.0, 62.0, 90, 4, 10, 2]
    platforms.engaged_views["VIDret"] = 1200
    platforms.retention_curves["VIDret"] = [[i / 100, round(1.1 - i / 120, 3), 0.55] for i in range(1, 101)]
    from tests.test_analytics_learning import _publication

    with session_scope() as db:
        publication = _publication(db, account_id=account_id, external_id="VIDret", views=None, duration=30.0)
        snap = collector.collect(db, publication)
        assert snap.metrics["analytics_engagedViews"] == 1200
        assert 0.9 < snap.metrics["first_3s_retention"] < 1.1 and snap.metrics["replay_ratio"] > 1
        assert "Studio" in snap.unavailable["viewed_rate"]  # not offered by the API: never filled in
        sig = retention.signals(snap.metrics, "youtube")
        assert sig["engaged_view_rate"] == pytest.approx(1200 / 1900, abs=0.001)
    reports = [r for r in platforms.requests if r["path"] == "/v2/reports"]
    assert any(r["query"].get("dimensions") == "elapsedVideoTimeRatio" and r["query"]["filters"] == "video==VIDret"
               for r in reports)


@pytest.mark.contract
def test_no_retention_data_is_reported_as_unavailable(platforms):
    account_id = connect("youtube", "UC_OWNER")
    platforms.uploaded_videos["VIDnew"] = {"id": "VIDnew", "statistics": {"viewCount": "12"}}
    from tests.test_analytics_learning import _publication

    with session_scope() as db:
        publication = _publication(db, account_id=account_id, external_id="VIDnew", views=None, age_hours=1)
        snap = collector.collect(db, publication)
        assert "first_3s_retention" not in snap.metrics and "first_3s_retention" in snap.unavailable
        assert "analytics_engagedViews" in snap.unavailable


STUDIO_CSV = """Content,Video title,Video publish time,Views,Stayed to watch (%),Average percentage viewed (%),Average view duration
Total,,,5210,,,
{vid},Never compete on price,"Nov 20, 2026",1840,42.5,61.3,0:00:17
zzzzzzzzzzz,Some other channel video,"Nov 1, 2026",3370,80.0,90.0,0:00:20
"""


def test_studio_csv_import_matches_clips_and_flags_a_flop(tmp_path):
    pid = _published_like(tmp_path, title="Never compete on price #shorts", hours_ago=30)
    with session_scope() as db:
        pub = db.execute(select(Publication).where(Publication.package_id == pid)).scalar_one()
        vid = pub.external_id
        result = retention.import_rows(db, retention.parse_csv(STUDIO_CSV.format(vid=vid)), platform="youtube")
    assert result.imported == 1 and result.unmatched == ["zzzzzzzzzzz"] and result.skipped
    assert result.columns["viewed_rate"] == "Stayed to watch (%)"
    with session_scope() as db:
        snap = db.execute(select(MetricSnapshot).where(MetricSnapshot.publication_id == pub.id)).scalar_one()
        assert snap.metrics == {"views": 1840, "viewed_rate": 0.425, "swipe_away_rate": 0.575,
                                "average_percentage_viewed": 61.3, "average_view_duration_s": 17.0}
        assert snap.source == "csv_import"
        cfg = app_settings.get_section(db, "shorts")
        report = retention.evaluate(db, cfg)
        clip = db.get(Clip, db.get(OutputPackage, pid).clip_id)
        info = clip.shorts["analytics"]["youtube"]
    assert info["signals"]["viewed_rate"] == 0.425 and info["flags"] and "42% viewed" in info["flags"][0]
    assert info["suggestion"] == "re-edit the opening (first 3 seconds)"
    assert report["flops"][0]["clip_id"] == str(clip.id)
    assert "first_3s_retention" in info["missing"]


def test_import_by_title_and_manual_entry(tmp_path):
    pid = _published_like(tmp_path, title="Never compete on price #shorts", hours_ago=80)
    rows = [{"Video title": "Never compete on price", "Viewed (%)": "18%"}]
    with session_scope() as db:
        result = retention.import_rows(db, rows)
        assert result.imported == 1
        pub = db.execute(select(Publication).where(Publication.package_id == pid)).scalar_one()
        snap = retention.manual_entry(db, pub.id, {"first_3s_retention": 35})
        assert snap.metrics == {"first_3s_retention": 0.35} and snap.source == "manual"
        with pytest.raises(ValueError):
            retention.manual_entry(db, pub.id, {"made_up_metric": 3})
        cfg = app_settings.get_section(db, "shorts")
        retention.evaluate(db, cfg)
        info = db.get(Clip, db.get(OutputPackage, pid).clip_id).shorts["analytics"]["youtube"]
    assert info["suggestion"].startswith("consider deleting")  # 18 % viewed after 80 h
    assert len(info["flags"]) == 2


def test_styles_are_ranked_only_with_enough_posts(tmp_path):
    values = [0.8, 0.75, 0.82, 0.3, 0.35, 0.28]
    templates = ["keyword_question"] * 3 + ["keyword_promise"] * 3
    with session_scope() as db:
        for value, template in zip(values, templates, strict=True):
            pid = _published_like(tmp_path, title=f"T {uuid.uuid4().hex[:6]} #shorts", hours_ago=40)
            package = db.get(OutputPackage, pid)
            clip = db.get(Clip, package.clip_id)
            clip.shorts = {"packaging_variants": [{"title": package.title.replace(" #shorts", ""), "template": template}]}
            pub = db.execute(select(Publication).where(Publication.package_id == pid)).scalar_one()
            db.add(MetricSnapshot(publication_id=pub.id, platform="youtube", metrics={"viewed_rate": value},
                                  unavailable={}, source="csv_import"))
        db.flush()
        cfg = app_settings.get_section(db, "shorts")
        report = retention.evaluate(db, cfg)
        boost = retention.template_boost(db, cfg)
    ranked = [r for r in report["ranking"] if r["dimension"] == "title_template"]
    assert ranked[0]["style"] == "keyword_question" and ranked[0]["enough_data"] and ranked[0]["posts"] == 3
    assert boost["keyword_question"] > 0 > boost["keyword_promise"] and abs(boost["keyword_question"]) <= 0.1
    assert report["segments"] and report["with_retention"] == 6


def test_import_api_and_report(owner_client, tmp_path):
    pid = _published_like(tmp_path, title="Never compete on price #shorts", hours_ago=30)
    with session_scope() as db:
        vid = db.execute(select(Publication).where(Publication.package_id == pid)).scalar_one().external_id
    response = owner_client.post("/api/analytics/import", data={"platform": "youtube"},
                                 files={"file": ("studio.csv", STUDIO_CSV.format(vid=vid).encode(), "text/csv")})
    assert response.status_code == 200, response.text
    assert response.json()["imported"] == 1 and response.json()["unmatched"] == ["zzzzzzzzzzz"]
    report = owner_client.get("/api/analytics/retention").json()
    assert report["flops"] and report["with_retention"] == 1
    bad = owner_client.post("/api/analytics/import", files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")})
    assert bad.status_code == 200 and bad.json()["imported"] == 0


def test_p2_is_off_by_default():
    with session_scope() as db:
        cfg = app_settings.get_section(db, "shorts")
    assert not (cfg.originality_gate or cfg.retention_loop or cfg.learn_from_retention)
