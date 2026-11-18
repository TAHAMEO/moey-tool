"""Analytics collection (contract) and the learning engine (synthetic fixtures).

Learning tests use generated publication/metric fixtures to exercise the
statistics and the strategy lifecycle; they are test data, never shown as
real performance.
"""

from __future__ import annotations

import random
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from clipforge.analytics import collect as collector
from clipforge.analytics.compare import dataset, permutation_test
from clipforge.db.base import session_scope, utcnow
from clipforge.db.models import (
    Clip,
    Experiment,
    LearningRecord,
    MetricSnapshot,
    OutputPackage,
    Project,
    Publication,
    Render,
    Source,
    Strategy,
)
from clipforge.learning import engine
from tests.conftest import connect


def _publication(db, *, platform="youtube", duration=25.0, views=1000.0, account_id=None, strategy_id=None, variant=None,
                 external_id=None, age_hours=48.0, extra_metrics=None, scores=None):
    source = Source(platform="upload", source_type="file", external_id=uuid.uuid4().hex)
    db.add(source)
    db.flush()
    project = Project(source_id=source.id, title="p", request={})
    db.add(project)
    db.flush()
    clip = Clip(project_id=project.id, source_id=source.id, rank=1, start_s=0, end_s=duration, hook="Here is the tip.",
                transcript_text="x", scores=scores or {"hook": 0.5, "information": 0.5}, strategy_id=strategy_id,
                experiment_variant=variant, features={"question_hook": False, "number_in_hook": False, "face_ratio": 0.7})
    db.add(clip)
    db.flush()
    render = Render(clip_id=clip.id, aspect="9:16", status="done", edit_decisions={"kept_intervals": [[0, duration]]})
    db.add(render)
    db.flush()
    package = OutputPackage(clip_id=clip.id, render_id=render.id, platform=platform, title=f"clip {duration:.0f}s")
    db.add(package)
    db.flush()
    publication = Publication(package_id=package.id, account_id=account_id, platform=platform,
                              idempotency_key=uuid.uuid4().hex, status="published", external_id=external_id or uuid.uuid4().hex[:11],
                              published_at=utcnow() - timedelta(hours=age_hours))
    db.add(publication)
    db.flush()
    if views is not None:
        db.add(MetricSnapshot(publication_id=publication.id, platform=platform, age_hours=age_hours,
                              metrics={"views": views, "likes": views * 0.05, "comments": views * 0.01, **(extra_metrics or {})},
                              unavailable={}, source="test-fixture"))
    return publication


@pytest.mark.contract
def test_youtube_metrics_collected_without_inventing(platforms):
    account_id = connect("youtube", "UC_OWNER")
    platforms.uploaded_videos["VIDabc"] = {"id": "VIDabc", "statistics": {"viewCount": "1520", "likeCount": "88", "commentCount": "7"}}
    platforms.analytics_rows["VIDabc"] = [1400, 310.5, 13.2, 72.4, 85, 6, 12, 3]
    with session_scope() as db:
        publication = _publication(db, account_id=account_id, external_id="VIDabc", views=None, age_hours=30)
        snapshot = collector.collect(db, publication)
        assert snapshot.metrics["views"] == 1520 and snapshot.metrics["likes"] == 88
        assert snapshot.metrics["analytics_averageViewPercentage"] == 72.4
        assert "impressions" in snapshot.unavailable and "Studio" in snapshot.unavailable["impressions"]
        assert "saves" in snapshot.unavailable


@pytest.mark.contract
def test_instagram_insights_marks_unsupported_metrics(platforms):
    account_id = connect("instagram", platforms.ig_user_id, meta={"account_type": "BUSINESS"})
    platforms.ig_insights["1790009"] = {"views": 900, "reach": 700, "likes": 40, "comments": 3, "shares": 5, "saved": 9,
                                        "total_interactions": 57}
    with session_scope() as db:
        publication = _publication(db, platform="instagram", account_id=account_id, external_id="1790009", views=None)
        snapshot = collector.collect(db, publication)
        assert snapshot.metrics == {"views": 900, "reach": 700, "likes": 40, "comments": 3, "shares": 5, "saved": 9,
                                    "total_interactions": 57}
        assert set(snapshot.unavailable) >= {"ig_reels_avg_watch_time", "ig_reels_video_view_total_time", "impressions"}


def test_no_data_means_no_findings():
    with session_scope() as db:
        result = engine.run(db)
        assert result == {"findings": 0, "experiments_concluded": 0, "experiments_started": 0}
        assert db.execute(select(LearningRecord)).first() is None
        assert db.execute(select(Strategy)).scalar_one().status == "active"  # default v1 seeded


def test_permutation_test_sanity():
    effect, p = permutation_test([1.0, 1.1, 0.9, 1.2, 1.0, 1.1], [0.0, 0.1, -0.1, 0.2, 0.0, 0.1])
    assert effect == pytest.approx(1.0, abs=0.01) and p < 0.01
    _, p_null = permutation_test([1, 2, 3, 4, 5], [1, 2, 3, 4, 5])
    assert p_null > 0.9


def test_learning_loop_correlation_experiment_and_promotion():
    rng = random.Random(3)
    with session_scope() as db:
        v1 = engine.ensure_default_strategy(db)
        for _ in range(12):  # short clips do better in this fixture
            _publication(db, duration=rng.uniform(15, 29), views=rng.uniform(1800, 2600), strategy_id=v1.id)
        for _ in range(12):
            _publication(db, duration=rng.uniform(35, 58), views=rng.uniform(700, 1200), strategy_id=v1.id)
    with session_scope() as db:
        result = engine.run(db)
        assert result["findings"] >= 1 and result["experiments_started"] == 1
        record = db.execute(select(LearningRecord).where(LearningRecord.evidence["dimension"].astext == "duration")).scalar_one()
        assert record.kind == "correlation" and record.causal_claim is False
        assert record.evidence["favours"] == "a" and record.evidence["p_value"] < 0.1 and record.evidence["n_a"] == 12
        assert "not proven causation" in record.limitations and record.next_experiment
        experiment = db.execute(select(Experiment)).scalar_one()
        variant = db.get(Strategy, experiment.variant_strategy_id)
        assert variant.status == "experiment" and variant.parent_id == v1.id and variant.params["target_seconds"] == 24.0
        assert db.get(Strategy, v1.id).status == "active"  # a correlation alone never replaces the strategy
        # Deterministic randomized assignment.
        project_id = uuid.uuid4()
        assert engine.assign_strategy(db, project_id) == engine.assign_strategy(db, project_id)
        experiment_id, control_id, variant_id = experiment.id, experiment.control_strategy_id, variant.id
    with session_scope() as db:  # experiment results arrive: variant wins
        for _ in range(9):
            _publication(db, duration=24, views=rng.uniform(2500, 3200), strategy_id=variant_id, variant="B")
            _publication(db, duration=40, views=rng.uniform(900, 1400), strategy_id=control_id, variant="A")
    with session_scope() as db:
        engine.run(db)
        experiment = db.get(Experiment, experiment_id)
        assert experiment.status == "concluded" and experiment.result["winner"] == "variant"
        assert db.get(Strategy, variant_id).status == "active"
        assert db.get(Strategy, control_id).status == "retired"  # versioned, never overwritten
        result_record = db.execute(select(LearningRecord).where(LearningRecord.kind == "experiment_result")).scalar_one()
        assert result_record.causal_claim is True and "Randomized" in result_record.limitations
        rows = dataset(db)
        assert all(r.relative is not None for r in rows)


def test_learning_api_and_chat(owner_client):
    with session_scope() as db:
        engine.ensure_default_strategy(db)
    strategies = owner_client.get("/api/learning/strategies").json()
    assert strategies[0]["version"] == 1 and strategies[0]["status"] == "active"
    assert owner_client.get("/api/learning/records").json() == []
    conv = owner_client.post("/api/chat/conversations", json={}).json()["id"]
    reply = owner_client.post(f"/api/chat/conversations/{conv}/messages", json={"text": "what have you learned so far?"}).json()
    assert "haven't recorded any lessons yet" in reply["content"]
    analytics = owner_client.post(f"/api/chat/conversations/{conv}/messages", json={"text": "how did my videos perform?"}).json()
    assert "No published videos yet" in analytics["content"]
