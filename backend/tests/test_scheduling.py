"""Scheduled publishing: exact go-live times on YouTube and Instagram.

Plain-language time parsing is a unit test. Everything that talks to YouTube or
Instagram is a CONTRACT test against the local fake of their documented APIs.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from clipforge.db.base import session_scope, utcnow
from clipforge.db.models import Job, OutputPackage, Project, Publication
from clipforge.jobs import queue
from clipforge.jobs.worker import Worker
from clipforge.publishing import schedule, service
from clipforge.publishing.when import describe, parse
from clipforge.services import app_settings, control
from tests.conftest import connect
from tests.test_publishing import _make_package

SAT = datetime(2026, 11, 21, 14, 30, tzinfo=UTC)  # a Saturday, 14:30 in London


def _p(text: str, now: datetime = SAT, zone: str = "Europe/London"):
    found = parse(text, now=now, zone=zone)
    return None if found is None else found.at.astimezone(__import__("zoneinfo").ZoneInfo(zone))


# ---------------------------------------------------------------- parsing (unit)
@pytest.mark.parametrize("text,expected", [
    ("publish clip 1 tomorrow at 6pm", "2026-11-22 18:00"),
    ("schedule it for friday 9:30", "2026-11-27 09:30"),
    ("post tonight", "2026-11-21 20:00"),
    ("post on 25 November at 18:00", "2026-11-25 18:00"),
    ("post 2026-12-01 08:15", "2026-12-01 08:15"),
    ("post at 6", "2026-11-21 18:00"),  # bare 1–7 means the evening
    ("post at 9", "2026-11-22 09:00"),  # 09:00 today has passed → tomorrow
    ("post on sat at 9", "2026-11-28 09:00"),  # said on Saturday after 9 → next Saturday
    ("post this saturday at 3pm", "2026-11-21 15:00"),
    ("post at noon", "2026-11-22 12:00"),
    ("post on nov 3rd", "2027-11-03 09:00"),  # already past this year
    ("post in 3 days at 10am", "2026-11-24 10:00"),
    ("post them every day at 6pm starting tomorrow", "2026-11-22 18:00"),
])
def test_parse_publish_times(text, expected):
    assert _p(text).strftime("%Y-%m-%d %H:%M") == expected


@pytest.mark.parametrize("text", [
    "publish now", "make 3 clips in 9:16 and post them", "cut at 30 seconds and publish", "good morning, publish clip 1",
    "post 3 shorts under 40 seconds",
])
def test_parse_ignores_text_without_a_time(text):
    assert parse(text, now=SAT, zone="Europe/London") is None


def test_parse_relative_spacing_and_assumed_time():
    assert parse("post in 2 hours", now=SAT, zone="UTC").at == SAT + timedelta(hours=2)
    daily = parse("post them daily at 7am", now=SAT, zone="UTC")
    assert daily.every_minutes == 1440 and daily.at == datetime(2026, 11, 22, 7, 0, tzinfo=UTC)
    assert parse("one every 3 hours", now=SAT, zone="UTC").every_minutes == 180
    assert parse("post them 2 hours apart starting at 5pm", now=SAT, zone="UTC").every_minutes == 120
    assumed = parse("post tomorrow", now=SAT, zone="UTC")
    assert assumed.assumed_time and assumed.at == datetime(2026, 11, 22, 9, 0, tzinfo=UTC)
    # The same words mean a different instant in another time zone.
    assert parse("tomorrow at 6pm", now=SAT, zone="Asia/Karachi").at == datetime(2026, 11, 22, 13, 0, tzinfo=UTC)
    assert describe(datetime(2026, 11, 22, 18, 0, tzinfo=UTC), "UTC", now=SAT) == "tomorrow (Sun 22 Nov) at 18:00 UTC"


def test_normalise_rejects_past_naive_and_far_times():
    with pytest.raises(schedule.ScheduleError) as past:
        schedule.normalise(utcnow() - timedelta(hours=1))
    assert past.value.code == "past"
    with pytest.raises(schedule.ScheduleError):
        schedule.normalise(datetime(2030, 1, 1, 9, 0))  # no time zone
    with pytest.raises(schedule.ScheduleError):
        schedule.normalise(utcnow() + timedelta(days=400))
    assert schedule.plan(SAT, 3, 1440) == [SAT, SAT + timedelta(days=1), SAT + timedelta(days=2)]


# ------------------------------------------------------------- helpers (contract)
pytestmark_contract = pytest.mark.contract


def _set(package_id: uuid.UUID, **fields) -> None:
    with session_scope() as db:
        package = db.get(OutputPackage, package_id)
        for key, value in fields.items():
            setattr(package, key, value)


def _get(package_id: uuid.UUID) -> OutputPackage:
    with session_scope() as db:
        package = db.get(OutputPackage, package_id)
        db.expunge(package)
        return package


def _publication(package_id: uuid.UUID) -> Publication:
    with session_scope() as db:
        pub = db.execute(select(Publication).where(Publication.package_id == package_id)).scalar_one()
        db.expunge(pub)
        return pub


def _run_default(max_jobs: int = 1) -> None:
    import clipforge.handlers  # noqa: F401

    Worker(["default"], worker_id=f"sched-{uuid.uuid4().hex[:4]}").run(max_jobs=max_jobs, idle_exit_s=3)


def _jobs(package_id: uuid.UUID) -> list[Job]:
    with session_scope() as db:
        jobs = db.execute(select(Job).where(Job.type == "workflow.publish", Job.payload["package_id"].astext == str(package_id))
                          .order_by(Job.created_at)).scalars().all()
        for job in jobs:
            db.expunge(job)
        return jobs


def _in(minutes: int) -> datetime:
    return (utcnow() + timedelta(minutes=minutes)).replace(second=0, microsecond=0)


# ------------------------------------------------------------- YouTube scheduler
@pytest.mark.contract
def test_youtube_public_post_is_handed_to_youtubes_scheduler_then_confirmed(platforms, tmp_path, owner_client):
    connect("youtube", "UC_OWNER")
    package_id = _make_package(tmp_path, "youtube")
    _set(package_id, privacy="public")
    when = _in(120)
    res = owner_client.post(f"/api/packages/{package_id}/publish", json={"scheduled_at": when.isoformat()})
    assert res.status_code == 200, res.text
    assert res.json()["mode"] == "youtube"
    _run_default()
    job = _jobs(package_id)[0]
    assert job.status == "completed" and job.status_label == "Scheduled", job.error
    meta = next(iter(platforms.uploads.values()))["meta"]
    assert meta["status"]["privacyStatus"] == "private"
    assert datetime.fromisoformat(meta["status"]["publishAt"]) == when
    pub = _publication(package_id)
    assert pub.status == "scheduled" and pub.scheduled_at == when and pub.published_at is None
    assert _get(package_id).status == "scheduled"
    # Nothing happens before the time (the reconciler spends no quota on it).
    schedule.reconcile(now=when - timedelta(minutes=30))
    assert platforms.video_updates == [] and _publication(package_id).status == "scheduled"
    # YouTube publishes it at publishAt; CLIPFORGE confirms it and only then marks it published.
    platforms.go_live(pub.external_id)
    schedule.reconcile(now=when + timedelta(minutes=2))
    pub = _publication(package_id)
    assert pub.status == "published" and pub.published_at == when
    assert _get(package_id).status == "published"
    with session_scope() as db:
        offsets = {j.payload["offset_hours"] for j in db.execute(select(Job).where(Job.type == "analytics.collect")).scalars()}
        assert offsets == {1, 24, 72, 168, 672}
    upcoming = owner_client.get("/api/schedule").json()
    assert upcoming["items"] == []


@pytest.mark.contract
def test_moving_editing_holding_and_cancelling_a_video_waiting_on_youtube(platforms, tmp_path, owner_client):
    connect("youtube", "UC_OWNER")
    package_id = _make_package(tmp_path, "youtube")
    _set(package_id, privacy="public")
    first = _in(180)
    owner_client.post(f"/api/packages/{package_id}/publish", json={"scheduled_at": first.isoformat()})
    _run_default()
    video_id = _publication(package_id).external_id
    # Move it: the reconcile kick updates publishAt on YouTube (no new upload).
    later = _in(300)
    res = owner_client.post(f"/api/packages/{package_id}/publish", json={"scheduled_at": later.isoformat()})
    assert res.status_code == 200 and res.json()["job_id"] is None
    schedule.reconcile()
    update = platforms.video_updates[-1]
    assert update["part"] == "status" and datetime.fromisoformat(update["body"]["publishAt"]) == later
    assert update["body"]["selfDeclaredMadeForKids"] is False  # other status values are preserved
    assert _publication(package_id).scheduled_at == later and len(platforms.uploads) == 1
    # Edit the title while it waits: the waiting video gets the new text.
    owner_client.patch(f"/api/packages/{package_id}", json={"title": "A better title #shorts"})
    schedule.reconcile()
    snippet = [u for u in platforms.video_updates if u["part"] == "snippet"][-1]["body"]
    assert snippet["title"] == "A better title #shorts" and snippet["categoryId"] == "22"
    # Hold publishing: the video goes back to private without a publish time; resume restores it.
    with session_scope() as db:
        control.apply(db, "disable_integration", user_id=None, integration="publish:youtube")
    schedule.reconcile()
    status = platforms.uploaded_videos[video_id]["status"]
    assert status["privacyStatus"] == "private" and "publishAt" not in status
    assert _publication(package_id).status == "held" and _get(package_id).status == "scheduled"
    with session_scope() as db:
        control.apply(db, "enable_integration", user_id=None, integration="publish:youtube")
    schedule.reconcile()
    assert datetime.fromisoformat(platforms.uploaded_videos[video_id]["status"]["publishAt"]) == later
    assert _publication(package_id).status == "scheduled"
    # Cancel: it stays on YouTube as private, and nothing will publish it.
    res = owner_client.post(f"/api/packages/{package_id}/unschedule")
    assert res.status_code == 200 and res.json()["remote_private"] is True
    schedule.reconcile()
    status = platforms.uploaded_videos[video_id]["status"]
    assert status["privacyStatus"] == "private" and "publishAt" not in status
    assert _publication(package_id).status == "cancelled"
    package = _get(package_id)
    assert package.status == "ready" and package.scheduled_at is None


@pytest.mark.contract
def test_publish_now_on_a_waiting_youtube_video_makes_it_live(platforms, tmp_path, owner_client):
    connect("youtube", "UC_OWNER")
    package_id = _make_package(tmp_path, "youtube")
    _set(package_id, privacy="public")
    owner_client.post(f"/api/packages/{package_id}/publish", json={"scheduled_at": _in(240).isoformat()})
    _run_default()
    res = owner_client.post(f"/api/packages/{package_id}/publish")
    assert res.status_code == 200
    schedule.reconcile()
    pub = _publication(package_id)
    assert pub.status == "published" and len(platforms.uploads) == 1
    assert platforms.uploaded_videos[pub.external_id]["status"]["privacyStatus"] == "public"


@pytest.mark.contract
def test_unlisted_youtube_post_is_uploaded_early_and_switched_at_the_minute(platforms, tmp_path, monkeypatch):
    monkeypatch.setattr(service, "SLOT_POLL_S", 0.2)
    connect("youtube", "UC_OWNER")
    package_id = _make_package(tmp_path, "youtube")
    when = utcnow() + timedelta(seconds=4)
    _set(package_id, privacy="unlisted", status="scheduled", scheduled_at=when)
    with session_scope() as db:
        assert schedule.mode(db.get(OutputPackage, package_id)) == "held"
        schedule.ensure_job(db, db.get(OutputPackage, package_id))
    _run_default()
    assert utcnow() >= when
    meta = next(iter(platforms.uploads.values()))["meta"]
    assert meta["status"]["privacyStatus"] == "private" and "publishAt" not in meta["status"]
    switch = platforms.video_updates[-1]
    assert switch["part"] == "status" and switch["body"]["privacyStatus"] == "unlisted"
    assert _publication(package_id).status == "published" and _get(package_id).status == "published"


# ------------------------------------------------------------------- Instagram
@pytest.mark.contract
def test_instagram_reel_is_prepared_early_and_published_at_the_exact_minute(platforms, tmp_path, monkeypatch):
    monkeypatch.setattr(service, "IG_POLL_INTERVAL_S", 0.1)
    monkeypatch.setattr(service, "SLOT_POLL_S", 0.2)
    connect("instagram", platforms.ig_user_id, meta={"account_type": "BUSINESS"})
    package_id = _make_package(tmp_path, "instagram")
    when = utcnow() + timedelta(seconds=4)
    _set(package_id, status="scheduled", scheduled_at=when)
    with session_scope() as db:
        job = schedule.ensure_job(db, db.get(OutputPackage, package_id))
        assert job.run_after <= utcnow() + timedelta(seconds=1)  # within the 10-minute preparation lead
    _run_default()
    assert platforms.containers, "the Reel container was prepared ahead of time"
    assert len(platforms.publish_calls) == 1 and platforms.publish_calls[0] >= when.timestamp()
    assert _publication(package_id).status == "published"


@pytest.mark.contract
def test_instagram_post_later_waits_in_the_queue_until_its_preparation_time(platforms, tmp_path, owner_client):
    connect("instagram", platforms.ig_user_id, meta={"account_type": "BUSINESS"})
    package_id = _make_package(tmp_path, "instagram")
    when = _in(180)
    res = owner_client.post(f"/api/packages/{package_id}/publish", json={"scheduled_at": when.isoformat()})
    assert res.status_code == 200 and res.json()["mode"] == "held"
    job = _jobs(package_id)[0]
    assert job.status == "queued" and job.run_after == when - schedule.PREP_LEAD and job.status_label == "Scheduled"
    _run_default()  # nothing is due
    assert platforms.containers == {}
    # Moving the time moves the same job (no duplicates).
    later = _in(400)
    owner_client.post(f"/api/packages/{package_id}/publish", json={"scheduled_at": later.isoformat()})
    jobs = [j for j in _jobs(package_id) if j.status in queue.ACTIVE]
    assert len(jobs) == 1 and jobs[0].run_after == later - schedule.PREP_LEAD
    listing = owner_client.get("/api/schedule").json()
    assert listing["items"][0]["package_id"] == str(package_id) and listing["items"][0]["mode"] == "held"
    # Cancelling removes the queued job.
    owner_client.post(f"/api/packages/{package_id}/unschedule")
    assert all(j.status == "cancelled" for j in _jobs(package_id))
    assert _get(package_id).status == "ready"


@pytest.mark.contract
def test_post_that_would_go_out_too_late_waits_for_the_owner(platforms, tmp_path):
    connect("instagram", platforms.ig_user_id, meta={"account_type": "BUSINESS"})
    package_id = _make_package(tmp_path, "instagram")
    _set(package_id, status="scheduled", scheduled_at=utcnow() - timedelta(hours=5))  # the computer was off
    with session_scope() as db:
        schedule.ensure_job(db, db.get(OutputPackage, package_id))
    _run_default()
    job = _jobs(package_id)[0]
    assert job.status == "failed" and job.status_label == "Missed its time" and "Pick a new time" in job.error["message"]
    assert _get(package_id).status == "missed" and platforms.containers == {}


@pytest.mark.contract
def test_held_post_keeps_its_time_while_publishing_is_on_hold(platforms, tmp_path):
    connect("instagram", platforms.ig_user_id, meta={"account_type": "BUSINESS"})
    package_id = _make_package(tmp_path, "instagram")
    _set(package_id, status="scheduled", scheduled_at=utcnow() + timedelta(minutes=5))
    with session_scope() as db:
        control.apply(db, "disable_integration", user_id=None, integration="publish:instagram")
        schedule.ensure_job(db, db.get(OutputPackage, package_id))
    _run_default(max_jobs=2)  # the reconcile kick from the control change, then the publish job
    job = _jobs(package_id)[0]
    assert job.status == "completed" and job.status_label == "On hold"
    assert _get(package_id).status == "scheduled" and platforms.containers == {}
    schedule.reconcile()
    assert len(_jobs(package_id)) == 1  # no new job while on hold
    with session_scope() as db:
        control.apply(db, "enable_integration", user_id=None, integration="publish:instagram")
    schedule.reconcile()
    assert [j.status for j in _jobs(package_id)] == ["completed", "queued"]


# ------------------------------------------------------------ limits and controls
@pytest.mark.contract
def test_daily_limit_is_checked_for_the_chosen_day(platforms, tmp_path, owner_client):
    with session_scope() as db:
        app_settings.update_section(db, "publishing", {"max_per_day_instagram": 1, "timezone": "Europe/London"})
    first, second = _make_package(tmp_path, "instagram"), _make_package(tmp_path, "instagram")
    day = _in(60 * 24 * 3).replace(hour=10, minute=0)
    assert owner_client.post(f"/api/packages/{first}/publish", json={"scheduled_at": day.isoformat()}).status_code == 200
    blocked = owner_client.post(f"/api/packages/{second}/publish", json={"scheduled_at": (day + timedelta(hours=3)).isoformat()})
    assert blocked.status_code == 409 and "daily limit is 1" in blocked.json()["error"]["message"]
    assert _get(second).status == "ready"
    nextday = owner_client.post(f"/api/packages/{second}/publish", json={"scheduled_at": (day + timedelta(days=1)).isoformat()})
    assert nextday.status_code == 200
    past = owner_client.post(f"/api/packages/{second}/publish", json={"scheduled_at": "2020-01-01T10:00:00Z"})
    assert past.status_code == 400 and past.json()["error"]["code"] == "past"


@pytest.mark.contract
def test_emergency_stop_cancels_schedules(platforms, tmp_path, owner_client):
    connect("youtube", "UC_OWNER")
    held = _make_package(tmp_path, "instagram")
    owner_client.post(f"/api/packages/{held}/publish", json={"scheduled_at": _in(90).isoformat()})
    waiting = _make_package(tmp_path, "youtube")
    _set(waiting, privacy="public")
    owner_client.post(f"/api/packages/{waiting}/publish", json={"scheduled_at": _in(90).isoformat()})
    _run_default()  # uploads the YouTube one with publishAt
    video_id = _publication(waiting).external_id
    with session_scope() as db:
        result = control.apply(db, "emergency_stop", user_id=None)
    assert result["unscheduled_posts"] == 2
    assert _get(held).status == "ready" and all(j.status == "cancelled" for j in _jobs(held))
    _run_default(max_jobs=3)  # the reconcile kick runs even while everything is stopped
    status = platforms.uploaded_videos[video_id]["status"]
    assert status["privacyStatus"] == "private" and "publishAt" not in status
    assert _publication(waiting).status == "cancelled"


@pytest.mark.contract
def test_quota_deferral_waits_for_the_pacific_midnight_reset(platforms, tmp_path):
    from zoneinfo import ZoneInfo

    connect("youtube", "UC_OWNER")
    package_id = _make_package(tmp_path, "youtube")
    platforms.quota_exceeded = True
    with session_scope() as db:
        queue.enqueue(db, "workflow.publish", {"package_id": str(package_id)})
    _run_default()
    later = [j for j in _jobs(package_id) if j.status == "queued"]
    assert len(later) == 1
    reset = later[0].run_after.astimezone(ZoneInfo("America/Los_Angeles"))
    assert (reset.hour, reset.minute) == (0, 10)
    assert _get(package_id).status == "scheduled"


# ------------------------------------------------------------------- approvals and chat
@pytest.mark.contract
def test_approval_can_schedule_clips_one_per_day(platforms, tmp_path, owner_client):
    packages = [_make_package(tmp_path, "youtube") for _ in range(3)]
    with session_scope() as db:
        ranks = []
        for rank, pid in enumerate(packages, 1):
            package = db.get(OutputPackage, pid)
            ranks.append({"package_id": str(pid), "platform": "youtube", "rank": rank})
        project = db.execute(select(Project)).scalars().first()
        job = Job(type="workflow.video", queue="default", payload={}, status="waiting", project_id=project.id,
                  checkpoint={"waiting": {"type": "approval", "packages": ranks}})
        db.add(job)
        db.flush()
        job_id = job.id
    start = _in(60 * 24).replace(hour=18, minute=0)
    res = owner_client.post(f"/api/jobs/{job_id}/approve", json={"approved": [str(p) for p in packages],
                                                                  "scheduled_at": start.isoformat(), "every_minutes": 1440})
    assert res.status_code == 200, res.text
    times = sorted(datetime.fromisoformat(t) for t in res.json()["scheduled"].values())
    assert times == [start, start + timedelta(days=1), start + timedelta(days=2)]
    with session_scope() as db:
        resumed = db.get(Job, job_id)
        assert resumed.status == "queued" and resumed.resume_value["scheduled"]
        assert all(db.get(OutputPackage, p).status == "scheduled" for p in packages)


@pytest.mark.contract
def test_chat_schedules_ready_clips_in_the_browser_time_zone(platforms, tmp_path, owner_client):
    package_id = _make_package(tmp_path, "youtube")
    conversation = owner_client.post("/api/chat/conversations", json={}).json()["id"]
    res = owner_client.post(f"/api/chat/conversations/{conversation}/messages",
                            json={"text": "publish clip 1 tomorrow at 6pm", "timezone": "Asia/Karachi"})
    assert res.status_code == 200, res.text
    reply = res.json()["content"]
    assert "Scheduled 1 post" in reply and "18:00 Asia/Karachi" in reply
    package = _get(package_id)
    local = package.scheduled_at.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Karachi"))
    assert package.status == "scheduled" and (local.hour, local.minute) == (18, 0)
    assert (local.date() - utcnow().astimezone(local.tzinfo).date()).days == 1
    assert [j.status for j in _jobs(package_id)] == ["queued"]
    past = owner_client.post(f"/api/chat/conversations/{conversation}/messages",
                             json={"text": "publish clip 1 on 2020-01-01 at 10:00"}).json()["content"]
    assert "already passed" in past


def test_project_status_follows_later_publications(tmp_path):
    package_id = _make_package(tmp_path, "youtube")
    with session_scope() as db:
        package = db.get(OutputPackage, package_id)
        project = db.execute(select(Project)).scalars().first()
        project.status = "ready"
        package.status, package.scheduled_at = "scheduled", utcnow() + timedelta(days=1)
        schedule.refresh_project_status(db, project.id)
        assert (project.status, project.status_label) == ("scheduled", "Publishing scheduled")
        package.status = "published"
        schedule.refresh_project_status(db, project.id)
        assert (project.status, project.status_label) == ("published", "Published")


def test_time_is_spent_waiting_only_in_small_steps(monkeypatch):
    # _wait_for_slot follows the package: cancelling while it waits ends the wait at once.
    from clipforge.db.models import Clip, Source

    monkeypatch.setattr(service, "SLOT_POLL_S", 0.05)
    with session_scope() as db:
        source = Source(platform="upload", source_type="file", external_id=uuid.uuid4().hex)
        db.add(source)
        db.flush()
        project = Project(source_id=source.id, title="t", request={})
        db.add(project)
        db.flush()
        clip = Clip(project_id=project.id, source_id=source.id, rank=1, start_s=0, end_s=5)
        db.add(clip)
        db.flush()
        package = OutputPackage(clip_id=clip.id, platform="instagram", status="scheduled",
                                scheduled_at=utcnow() + timedelta(minutes=5))
        db.add(package)
        db.flush()
        package_id = package.id
    import threading

    threading.Timer(0.3, lambda: _set(package_id, status="ready", scheduled_at=None)).start()
    started = time.monotonic()
    assert service._wait_for_slot(package_id) == ("cancelled", None)
    assert time.monotonic() - started < 3
