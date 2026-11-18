"""Storage clean-up honours the retention settings without breaking work in progress."""

from __future__ import annotations

from datetime import timedelta

from clipforge.db.base import session_scope, utcnow
from clipforge.db.models import MediaAsset, OutputPackage, Publication, Render
from clipforge.handlers import maintenance_jobs
from clipforge.storage import get_storage
from tests.conftest import connect
from tests.test_publishing import _make_package, _publish


def _age_render(package_id, days: int):
    with session_scope() as db:
        render = db.get(Render, db.get(OutputPackage, package_id).render_id)
        render.created_at = utcnow() - timedelta(days=days)
        return render.id, render.video_asset_id, render.clip_id


def test_finished_clips_expire_but_not_while_publishing(owner_client, platforms, tmp_path):
    owner_client.patch("/api/settings/retention", json={"keep_renders_days": 30})
    connect("youtube", platforms.channel_id)
    old = _make_package(tmp_path, "youtube")
    busy = _make_package(tmp_path, "youtube")
    fresh = _make_package(tmp_path, "youtube")
    old_render, old_video, _ = _age_render(old, 400)
    busy_render, _, _ = _age_render(busy, 400)
    fresh_render, _, _ = _age_render(fresh, 3)
    with session_scope() as db:  # an upload of `busy` is still in progress
        db.add(Publication(package_id=busy, platform="youtube", status="uploading", idempotency_key="k-busy"))
        key = db.get(MediaAsset, old_video).storage_key
    assert get_storage().exists(key)

    result = maintenance_jobs.cleanup(None)

    assert result["renders"] == 1
    with session_scope() as db:
        assert db.get(Render, old_render).status == "expired"
        assert db.get(MediaAsset, old_video).info["deleted"] is True
        assert db.get(Render, busy_render).status == "done"  # never pulled from under a running upload
        assert db.get(Render, fresh_render).status == "done"
        project_id = db.get(MediaAsset, old_video).project_id
    assert not get_storage().exists(key)
    renders = [r for c in owner_client.get(f"/api/projects/{project_id}").json()["clips"] for r in c["renders"]]
    assert renders and all(r["status"] == "expired" and r["video_url"] is None for r in renders)

    job = _publish(old)  # publishing an expired clip explains what to do
    assert job.status == "failed" and "storage clean-up" in job.error["message"]


def test_live_sessions_default_to_the_retention_setting(owner_client):
    owner_client.patch("/api/settings/retention", json={"live_buffer_minutes": 45})
    created = owner_client.post("/api/live/sessions", json={}).json()
    assert created["retention_minutes"] == 45
    explicit = owner_client.post("/api/live/sessions", json={"retention_minutes": 90}).json()
    assert explicit["retention_minutes"] == 90
