"""Folder batches: a folder picked in the browser, or a watched inbox folder, processed with one recipe."""

from __future__ import annotations

import os
import subprocess
import time
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from clipforge.batches import service
from clipforge.batches.recipe import Recipe
from clipforge.config import get_settings
from clipforge.db.base import session_scope, utcnow
from clipforge.db.models import Batch, BatchItem, Job, OutputPackage, Project, WatchFolder
from clipforge.jobs import queue
from clipforge.jobs.worker import Worker, run_due_schedules, seed_schedules
from clipforge.services import app_settings


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    root = tmp_path / "inbox"
    root.mkdir()
    monkeypatch.setenv("CLIPFORGE_INBOX_DIR", str(root))
    get_settings.cache_clear()
    yield root
    get_settings.cache_clear()


def _video(path, *, seconds: float = 1.0, tone: int = 440, old: bool = True):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x284:rate=10", "-f", "lavfi",
                    "-i", f"sine=frequency={tone}", "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True, timeout=60)
    if old:  # finished copying long ago
        stamp = time.time() - 600
        os.utime(path, (stamp, stamp))
    return path


def _media_worker(jobs: int = 10):
    import clipforge.handlers  # noqa: F401

    Worker(["media"], worker_id=f"batch-{uuid.uuid4().hex[:4]}").run(max_jobs=jobs, idle_exit_s=2)


# ------------------------------------------------------------------- recipe
def test_recipe_reads_like_a_chat_request(db):
    recipe = Recipe(instruction="make 3 shorts under 40 seconds about pricing", platforms=["youtube"], publish="auto",
                    privacy="public", schedule={"mode": "slots", "times": ["18:00", "9:00", "18:00"]})
    assert recipe.schedule.times == ["09:00", "18:00"]
    request = recipe.to_request(db, batch_id="b1")
    assert request["count"] == 3 and request["max_seconds"] == 40 and request["focus"] == "pricing"
    assert request["platforms"] == ["youtube"] and request["auto_publish"] and request["publish_requested"]
    assert request["schedule_slots"] and request["privacy"] == "public" and request["batch_id"] == "b1"
    explicit = Recipe(instruction="make 3 shorts", count=5).to_request(db, batch_id="b2")
    assert explicit["count"] == 5  # an explicit field wins over the instruction
    assert Recipe(publish="drafts").to_request(db, batch_id="b3")["publish_requested"] is False
    with pytest.raises(ValueError):
        Recipe(schedule={"mode": "slots", "times": ["25:00"]})


# ------------------------------------------------------------------- folders
def test_folder_paths_never_leave_the_inbox(inbox, tmp_path):
    (inbox / "Podcast").mkdir()
    outside = tmp_path / "secret"
    outside.mkdir()
    (inbox / "escape").symlink_to(outside)
    assert service.resolve_folder("Podcast") == (inbox / "Podcast").resolve()
    for bad in ("../secret", "Podcast/../../secret", "~root", "escape"):
        with pytest.raises(service.BatchError):
            service.resolve_folder(bad)
    _video(inbox / "Podcast" / "ep1.mp4")
    (inbox / "Podcast" / "notes.txt").write_text("not a video")
    listing = service.browse("")
    assert [f["name"] for f in listing["folders"]] == ["Podcast"]  # the symlink is not offered
    assert listing["folders"][0]["videos"] == 1


def test_watched_folder_takes_finished_videos_and_starts_projects(inbox, owner_client):
    folder = inbox / "Podcast"
    _video(folder / "Episode 1.mp4", tone=440)
    _video(folder / "season2" / "Episode 2.mp4", tone=550)
    _video(folder / "copy of episode 1.mp4", tone=440)  # same content, other name
    copying = _video(folder / "Episode 3.mp4", tone=660, old=False)  # still being written
    res = owner_client.post("/api/folders", json={"path": "Podcast", "recipe": {"instruction": "make 2 shorts",
                                                                                "publish": "drafts"}})
    assert res.status_code == 200, res.text
    batch_id = uuid.UUID(res.json()["id"])
    with session_scope() as db:
        watch = db.execute(select(WatchFolder)).scalar_one()
        assert service.scan_folder(db, watch) == 3
        assert list(watch.state["pending"]) == ["Episode 3.mp4"]
    _media_worker()
    with session_scope() as db:
        items = {i.filename: i for i in db.execute(select(BatchItem).where(BatchItem.batch_id == batch_id)).scalars()}
        assert set(items) == {"Episode 1.mp4", "season2/Episode 2.mp4", "copy of episode 1.mp4"}
        duplicate = [i for i in items.values() if i.status == "duplicate"]
        assert len(duplicate) == 1 and "Same video as" in duplicate[0].error
        started = [i for i in items.values() if i.status == "started"]
        assert len(started) == 2
        for item in started:
            project = db.get(Project, item.project_id)
            assert project.request["batch_id"] == str(batch_id) and project.request["count"] == 2
            assert project.request["publish_requested"] is False
            job = db.execute(select(Job).where(Job.project_id == project.id, Job.type == "workflow.video")).scalar_one()
            assert job.status == "queued" and job.priority == service.BATCH_PRIORITY
        # The file that was still being written is taken once it has stopped changing between two looks.
        watch = db.execute(select(WatchFolder)).scalar_one()
        assert copying.stat().st_mtime > time.time() - 60
        assert service.scan_folder(db, watch) == 1
        assert service.scan_folder(db, watch) == 0  # never twice
    assert all(p.exists() for p in folder.rglob("*.mp4"))  # the owner's files are left alone by default
    summary = owner_client.get(f"/api/batches/{batch_id}").json()
    assert summary["counts"]["total"] == 4 and summary["counts"]["skipped"] == 1
    assert summary["folder"]["path"] == "Podcast"
    listed = owner_client.get(f"/api/projects?batch_id={batch_id}").json()
    assert len(listed) == 2


def test_processed_videos_can_be_moved_and_existing_ones_skipped(inbox, owner_client):
    folder = inbox / "Gaming"
    _video(folder / "old clip.mp4")
    res = owner_client.post("/api/folders", json={"path": "Gaming", "recipe": {}, "include_existing": False,
                                                  "after_processing": "move"})
    assert res.status_code == 200, res.text
    with session_scope() as db:
        watch = db.execute(select(WatchFolder)).scalar_one()
        assert service.scan_folder(db, watch) == 0  # it was there before: left alone
    _video(folder / "new clip.mp4", tone=700)
    with session_scope() as db:
        watch = db.execute(select(WatchFolder)).scalar_one()
        assert service.scan_folder(db, watch) == 1
    _media_worker()
    assert (folder / "Processed" / "new clip.mp4").exists() and not (folder / "new clip.mp4").exists()
    assert (folder / "old clip.mp4").exists()
    with session_scope() as db:
        watch = db.execute(select(WatchFolder)).scalar_one()
        assert service.scan_folder(db, watch) == 0  # the Processed folder is never scanned


def test_watching_rules(inbox, owner_client):
    assert owner_client.post("/api/folders", json={"path": "Missing", "recipe": {}}).status_code == 409
    (inbox / "Vlog").mkdir()
    assert owner_client.post("/api/folders", json={"path": "Vlog", "recipe": {}}).status_code == 200
    again = owner_client.post("/api/folders", json={"path": "Vlog", "recipe": {}})
    assert again.status_code == 409 and "already being watched" in again.json()["error"]["message"]
    bad = owner_client.post("/api/folders", json={"path": "../x", "recipe": {}})
    assert bad.status_code == 409
    recipe = owner_client.post("/api/folders", json={"path": "", "recipe": {"publish": "sometimes"}})
    assert recipe.status_code == 422 and "publish" in recipe.json()["error"]["message"]
    listing = owner_client.get("/api/folders").json()
    folder_id = listing["watched"][0]["id"]
    assert listing["watched"][0]["name"] == "Vlog"
    assert owner_client.patch(f"/api/folders/{folder_id}", json={"enabled": False}).json()["enabled"] is False
    assert owner_client.delete(f"/api/folders/{folder_id}").status_code == 200
    assert owner_client.get("/api/folders").json()["watched"] == []


def test_folder_scans_only_run_when_something_is_watched(inbox):
    seed_schedules()
    with session_scope() as db:
        from clipforge.db.models import Schedule

        for row in db.execute(select(Schedule)).scalars():
            row.next_run_at = utcnow() - timedelta(seconds=1)
    run_due_schedules()
    with session_scope() as db:
        assert db.execute(select(Job).where(Job.type == "folders.scan")).first() is None
        (inbox / "Shorts").mkdir()
        service.create_watch_folder(db, path="Shorts", recipe=Recipe(), name=None, user_id=None)
        from clipforge.db.models import Schedule

        db.execute(select(Schedule).where(Schedule.name == "folders-scan")).scalar_one().next_run_at = utcnow() - timedelta(seconds=1)
    run_due_schedules()
    with session_scope() as db:
        assert db.execute(select(Job).where(Job.type == "folders.scan")).scalar_one().status == "queued"


# ------------------------------------------------------------- browser uploads
def test_folder_picked_in_the_browser(inbox, owner_client, tmp_path):
    res = owner_client.post("/api/batches", json={"name": "Trip videos", "recipe": {"count": 2, "publish": "approve"}})
    assert res.status_code == 200, res.text
    batch_id = res.json()["id"]
    assert res.json()["status"] == "open"
    for name, tone in (("day1.mp4", 300), ("day2.mp4", 350)):
        path = _video(tmp_path / name, tone=tone)
        with open(path, "rb") as handle:
            uploaded = owner_client.post("/api/chat/upload", files={"file": (name, handle, "video/mp4")}).json()
        added = owner_client.post(f"/api/batches/{batch_id}/items", json={"asset_id": uploaded["asset_id"], "filename": f"Trip/{name}"})
        assert added.status_code == 200 and added.json()["status"] == "started", added.text
    # A retried upload of the same file does not start a second project.
    again = owner_client.post(f"/api/batches/{batch_id}/items", json={"asset_id": uploaded["asset_id"], "filename": "Trip/day2.mp4"})
    assert again.json()["id"] == added.json()["id"]
    closed = owner_client.post(f"/api/batches/{batch_id}/close").json()
    assert closed["status"] == "running" and closed["counts"]["total"] == 2 and closed["counts"]["processing"] == 2
    paused = owner_client.post(f"/api/batches/{batch_id}/pause").json()
    assert paused["status"] == "paused"
    with session_scope() as db:
        statuses = {j.status for j in db.execute(select(Job).where(Job.type == "workflow.video")).scalars()}
        assert statuses == {"paused"}
    assert owner_client.post(f"/api/batches/{batch_id}/resume").json()["status"] == "running"
    cancelled = owner_client.post(f"/api/batches/{batch_id}/cancel").json()
    assert cancelled["status"] == "cancelled"
    with session_scope() as db:
        assert {j.status for j in db.execute(select(Job).where(Job.type == "workflow.video")).scalars()} == {"cancelled"}
    assert owner_client.get("/api/batches").json() == []  # cancelled batches are not listed


# ----------------------------------------------------------------- posting slots
def test_posting_slots_are_shared_by_all_videos_of_a_batch(tmp_path):
    from tests.test_publishing import _make_package

    with session_scope() as db:
        app_settings.update_section(db, "publishing", {"timezone": "Europe/London", "max_per_day_youtube": 1})
        batch = service.create_batch(db, name="b", origin="upload", user_id=None,
                                     recipe=Recipe(publish="auto", schedule={"mode": "slots", "times": ["09:00", "18:00"]}))
        batch_id = batch.id
    first = [_make_package(tmp_path, "youtube"), _make_package(tmp_path, "instagram")]  # one clip, two platforms
    second = [_make_package(tmp_path, "youtube")]
    with session_scope() as db:
        pkgs = [db.get(OutputPackage, p) for p in first]
        times, problems = service.allocate_slots(db, batch_id, [(pkgs[0], 1), (pkgs[1], 1)])
        assert not problems and len(set(times.values())) == 1  # both platforms at the same minute
        slot = next(iter(times.values()))
        local = slot.astimezone(ZoneInfo("Europe/London"))
        assert (local.hour, local.minute) in ((9, 0), (18, 0)) and slot > utcnow()
    with session_scope() as db:
        later, problems = service.allocate_slots(db, batch_id, [(db.get(OutputPackage, second[0]), 1)])
        nxt = next(iter(later.values()))
        assert not problems and nxt > slot
        # one YouTube post per day: the next YouTube clip moves to the following day
        assert nxt.astimezone(ZoneInfo("Europe/London")).date() > local.date()
        assert db.get(OutputPackage, second[0]).status == "scheduled"
        assert datetime.fromisoformat(db.get(Batch, batch_id).state["next_slot"]) > nxt


def test_batch_auto_publish_bypasses_approval_but_not_draft_only(tmp_path):
    from clipforge.handlers.video_workflow import publish_gate_node
    from tests.test_publishing import _make_package

    package_id = _make_package(tmp_path, "youtube")
    with session_scope() as db:
        batch = service.create_batch(db, name="b", origin="upload", user_id=None, recipe=Recipe(publish="auto"))
        project = db.execute(select(Project)).scalars().first()
        project.request = Recipe(publish="auto").to_request(db, batch_id=str(batch.id))
        project_id = project.id
    state = {"project_id": str(project_id), "access": {"method": "upload", "owned": True},
             "packages": [{"package_id": str(package_id), "platform": "youtube", "rank": 1, "qc_passed": True,
                           "review_words": 0}]}
    result = publish_gate_node(state, {"configurable": {"thread_id": "x"}})
    assert result["approved"] == [str(package_id)] and "Batch set to post automatically" in result["notes"][0]
    with session_scope() as db:
        app_settings.update_section(db, "publishing", {"policy": "draft_only"})
    assert publish_gate_node(state, {"configurable": {"thread_id": "x"}})["approved"] == []


def test_ingest_ignores_files_that_changed_after_they_were_seen(inbox):
    folder = inbox / "Clips"
    video = _video(folder / "a.mp4")
    with session_scope() as db:
        watch = service.create_watch_folder(db, path="Clips", recipe=Recipe(), name=None, user_id=None)
        assert service.scan_folder(db, watch) == 1
    _video(video, seconds=2.0)  # replaced before the copy started
    _media_worker()
    with session_scope() as db:
        assert db.execute(select(BatchItem)).first() is None  # dropped; the next scan takes the new version
        watch = db.execute(select(WatchFolder)).scalar_one()
        assert service.scan_folder(db, watch) == 1
    with session_scope() as db:
        assert queue.ACTIVE  # sanity
        assert db.execute(select(Job).where(Job.type == "batch.ingest_file", Job.status == "queued")).first() is not None
