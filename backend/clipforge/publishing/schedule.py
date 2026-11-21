"""Scheduled publishing: the exact date and time a post goes live.

Two mechanisms, chosen per post:

* **YouTube's own scheduler.** A *public* YouTube video scheduled at least
  15 minutes ahead is uploaded straight away as private with
  ``status.publishAt``; YouTube makes it public at that time — even if this
  computer is switched off by then. CLIPFORGE checks afterwards and marks it
  published only once YouTube reports it public.
* **Held.** Instagram's publishing API cannot schedule, and YouTube schedules
  only public videos, so these posts wait in the durable job queue. They are
  prepared ten minutes early (upload and processing, kept private) and made
  live at the exact minute. CLIPFORGE must be running at that time; a post
  that would go out more than ``late_grace_minutes`` late waits for the owner
  instead.

Controls: *emergency stop* cancels every schedule. *Hold publishing* keeps the
schedules but nothing goes out — videos already handed to YouTube's scheduler
are switched back to private until publishing resumes. *Pause* stops
CLIPFORGE's own work; posts already handed to YouTube still go live.

:func:`reconcile` runs every five minutes (and right after any change) and is
the single place that makes reality match the plan.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.base import session_scope, utcnow
from ..db.models import Clip, Job, OutputPackage, Project, Publication, SocialAccount
from ..jobs import queue
from ..platforms import google, youtube
from ..platforms.http import PlatformError
from ..services import app_settings, control

log = logging.getLogger(__name__)

NATIVE_MIN_LEAD = timedelta(minutes=15)  # less than this ahead: held, then made public at the time
PREP_LEAD = timedelta(minutes=10)  # held posts: upload + processing before the minute
MAX_AHEAD = timedelta(days=366)
CONFIRM_TIMEOUT = timedelta(hours=2)  # YouTube should have made it public well within this
PUBLISH_PRIORITY = 40  # due posts go ahead of routine work on the default queue
REMOTE_STATES = ("scheduled", "held")  # publications whose video waits, private, on YouTube


class ScheduleError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid"):
        super().__init__(message)
        self.code = code


# ----------------------------------------------------------------- helpers
def zone(db: Session) -> ZoneInfo:
    return ZoneInfo(app_settings.get_section(db, "publishing").timezone)


def normalise(when: datetime, *, now: datetime | None = None) -> datetime:
    if when.tzinfo is None:
        raise ScheduleError("The time needs a time zone (for example 2026-11-22T18:00:00+00:00).", code="naive")
    now = now or utcnow()
    when = when.astimezone(UTC).replace(second=0, microsecond=0)
    if when < now - timedelta(minutes=1):
        raise ScheduleError("That time has already passed — pick a time in the future.", code="past")
    if when > now + MAX_AHEAD:
        raise ScheduleError("Posts can be scheduled at most a year ahead.", code="too_far")
    return when


def uses_youtube_scheduler(package: OutputPackage, when: datetime | None, now: datetime | None = None) -> bool:
    now = now or utcnow()
    return (package.platform == "youtube" and (package.privacy or "private") == "public" and when is not None
            and when - now >= NATIVE_MIN_LEAD)


def start_at(package: OutputPackage, now: datetime | None = None) -> datetime:
    """When the publish job for this package should start."""
    now = now or utcnow()
    when = package.scheduled_at if package.status == "scheduled" else None
    if when is None or uses_youtube_scheduler(package, when, now):
        return now
    return max(now, when - PREP_LEAD)


def mode(package: OutputPackage, now: datetime | None = None) -> str:
    """'youtube' (YouTube's scheduler), 'held' (CLIPFORGE publishes at the time) or 'now'."""
    if package.status != "scheduled" or package.scheduled_at is None:
        return "now"
    return "youtube" if uses_youtube_scheduler(package, package.scheduled_at, now) else "held"


def _day_window(when: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    day: date = when.astimezone(tz).date()
    start = datetime.combine(day, time(0), tz)
    end = datetime.combine(day + timedelta(days=1), time(0), tz)
    return start.astimezone(UTC), end.astimezone(UTC)


def day_load(db: Session, platform: str, when: datetime, tz: ZoneInfo, *, exclude: uuid.UUID | None = None) -> int:
    """Posts on that local day: already published plus scheduled."""
    start, end = _day_window(when, tz)
    published = db.execute(select(func.count()).select_from(Publication).where(
        Publication.platform == platform, Publication.status == "published",
        Publication.published_at >= start, Publication.published_at < end)).scalar_one()
    query = select(func.count()).select_from(OutputPackage).where(
        OutputPackage.platform == platform, OutputPackage.status == "scheduled",
        OutputPackage.scheduled_at >= start, OutputPackage.scheduled_at < end)
    if exclude is not None:
        query = query.where(OutputPackage.id != exclude)
    return published + db.execute(query).scalar_one()


def daily_limit(db: Session, platform: str) -> int:
    publishing = app_settings.get_section(db, "publishing")
    return publishing.max_per_day_youtube if platform == "youtube" else publishing.max_per_day_instagram


def next_local_midnight(db: Session) -> datetime:
    tz = zone(db)
    tomorrow = utcnow().astimezone(tz).date() + timedelta(days=1)
    return datetime.combine(tomorrow, time(0, 15), tz).astimezone(UTC)


def publishing_allowed(db: Session, platform: str) -> bool:
    return control.integration_enabled(db, f"publish:{platform}")


def remote_publication(db: Session, package_id: uuid.UUID) -> Publication | None:
    """The YouTube upload that waits (private) for this package's time, if there is one."""
    return db.execute(select(Publication).where(Publication.package_id == package_id,
                                                Publication.status.in_(REMOTE_STATES),
                                                Publication.external_id.is_not(None))).scalars().first()


def active_jobs(db: Session, package_id: uuid.UUID, *, exclude: uuid.UUID | None = None) -> list[Job]:
    query = (select(Job).where(Job.type == "workflow.publish", Job.payload["package_id"].astext == str(package_id),
                               Job.status.in_(queue.ACTIVE))
             .order_by(Job.created_at).with_for_update())
    jobs = list(db.execute(query).scalars())
    return [j for j in jobs if j.id != exclude]


def ensure_job(db: Session, package: OutputPackage, *, user_id: uuid.UUID | None = None,
               exclude: uuid.UUID | None = None, not_before: datetime | None = None) -> Job | None:
    """Make sure exactly one publish job will act on this package, starting at the right moment.

    ``not_before`` holds the job back further (a daily limit or the YouTube quota reset).
    """
    now = utcnow()
    jobs = active_jobs(db, package.id, exclude=exclude)
    busy = [j for j in jobs if j.status in ("running", "waiting") or j.parent_id is not None]
    if busy:
        return busy[0]  # it re-reads the package's schedule before its next irreversible step
    run_after = max(start_at(package, now), not_before or now)
    label = "Scheduled" if run_after > now + timedelta(seconds=30) else "Preparing upload"
    if jobs:
        job, extra = jobs[0], jobs[1:]
        for other in extra:
            queue.cancel(db, other.id, "Replaced by the new schedule")
        job.run_after, job.status_label, job.priority = run_after, label, PUBLISH_PRIORITY
        return job
    clip = db.get(Clip, package.clip_id)
    return queue.enqueue(db, "workflow.publish", {"package_id": str(package.id)}, run_after=run_after,
                         dedupe_key=f"publish:{package.id}", project_id=clip.project_id if clip else None,
                         user_id=user_id, status_label=label, priority=PUBLISH_PRIORITY)


def kick(db: Session, *, at: datetime | None = None) -> None:
    """Run the reconciler now (or at a given minute) instead of waiting for its 5-minute cycle."""
    at = at or utcnow()
    queue.enqueue(db, "maintenance.publish_scheduled", {}, run_after=at, priority=PUBLISH_PRIORITY,
                  dedupe_key=f"publish-reconcile:{at.strftime('%Y%m%d%H%M')}", max_attempts=2)


# ------------------------------------------------------------ owner actions
def apply(db: Session, package: OutputPackage, when: datetime) -> datetime:
    """Validate and record a package's time, without starting anything (approvals start their own jobs)."""
    if package.status == "published":
        raise ScheduleError("This clip is already published.", code="published")
    when = normalise(when)
    tz = zone(db)
    limit = daily_limit(db, package.platform)
    load = day_load(db, package.platform, when, tz, exclude=package.id)
    if load >= limit:
        local = when.astimezone(tz)
        raise ScheduleError(
            f"{load} {'YouTube' if package.platform == 'youtube' else 'Instagram'} post(s) already go out on "
            f"{local.strftime('%a')} {local.day} {local.strftime('%b')} — your daily limit is {limit}. "
            "Pick another day or raise the limit in Settings → Publishing.", code="daily_limit")
    package.scheduled_at = when
    package.status = "scheduled"
    db.flush()
    return when


def schedule(db: Session, package: OutputPackage, when: datetime, *, user_id: uuid.UUID | None = None) -> dict:
    """Set (or move) the time a package goes live, and make sure it will be acted on."""
    when = apply(db, package, when)
    remote = remote_publication(db, package.id)
    job = None if remote is not None else ensure_job(db, package, user_id=user_id)
    if remote is not None:
        kick(db)
    if mode(package) == "held":
        kick(db, at=when)  # catches a post whose job could not run (for example, publishing on hold)
    refresh_project_status(db, db.get(Clip, package.clip_id).project_id)
    return {"package_id": str(package.id), "scheduled_at": when.isoformat(), "mode": mode(package),
            "job_id": str(job.id) if job else None}


def plan(start: datetime, count: int, every_minutes: int | None) -> list[datetime]:
    """Times for several posts: all at ``start``, or one every ``every_minutes``."""
    step = timedelta(minutes=every_minutes or 0)
    return [start + step * i for i in range(count)]


def apply_plan(db: Session, entries: list[tuple[OutputPackage, int]], start: datetime,
               every_minutes: int | None) -> dict[str, datetime]:
    """Give packages their times in clip order (``(package, clip rank)`` pairs).

    Both platforms of one clip share a time; the next clip follows ``every_minutes`` later.
    Raises :class:`ScheduleError` (nothing is half-applied: the caller's transaction rolls back).
    """
    ranks = sorted({rank for _package, rank in entries})
    slots = plan(start, len(ranks), every_minutes)
    times: dict[str, datetime] = {}
    for package, rank in sorted(entries, key=lambda e: (e[1], e[0].platform)):
        times[str(package.id)] = apply(db, package, slots[ranks.index(rank)])
    return times


def unschedule(db: Session, package: OutputPackage, *, reason: str = "Schedule cancelled") -> dict:
    """Cancel a scheduled post. A video already on YouTube stays there as private."""
    if package.status not in ("scheduled", "missed"):
        raise ScheduleError("This clip is not scheduled.", code="not_scheduled")
    package.scheduled_at = None
    package.status = "ready"
    for job in active_jobs(db, package.id):
        if job.parent_id is None and job.status in ("queued", "paused"):
            queue.cancel(db, job.id, reason)
    remote = remote_publication(db, package.id)
    if remote is not None:
        kick(db)
    refresh_project_status(db, db.get(Clip, package.clip_id).project_id)
    return {"package_id": str(package.id), "remote_private": remote is not None}


def cancel_all(db: Session, *, reason: str) -> int:
    """Emergency stop: every schedule is cancelled; waiting YouTube uploads are kept private."""
    count = 0
    for package in db.execute(select(OutputPackage).where(OutputPackage.status == "scheduled")).scalars():
        package.status = "ready"
        package.scheduled_at = None
        count += 1
    if count or db.execute(select(func.count()).select_from(Publication)
                           .where(Publication.status.in_(REMOTE_STATES))).scalar_one():
        kick(db)
    return count


# ---------------------------------------------------------- project status
def refresh_project_status(db: Session, project_id: uuid.UUID | None) -> None:
    """Keep the project's label in step with posts that went out (or were scheduled) later."""
    if project_id is None:
        return
    project = db.get(Project, project_id)
    if project is None or project.status not in ("ready", "scheduled", "published", "partially_published", "completed"):
        return
    packages = db.execute(select(OutputPackage).join(Clip, Clip.id == OutputPackage.clip_id)
                          .where(Clip.project_id == project_id)).scalars().all()
    published = sum(p.status == "published" for p in packages)
    scheduled = sum(p.status == "scheduled" for p in packages)
    failed = sum(p.status in ("failed", "missed") for p in packages)
    if scheduled:
        project.status = "scheduled"
        project.status_label = "Publishing scheduled" if not published else f"Published {published}, {scheduled} scheduled"
    elif published and not failed:
        project.status, project.status_label = "published", "Published"
    elif published:
        project.status, project.status_label = "partially_published", f"Published {published}, {failed} not published"
    elif project.status in ("scheduled", "published", "partially_published"):
        project.status, project.status_label = "ready", "Clips ready"


# --------------------------------------------- YouTube videos waiting on YouTube
def desired_youtube_status(package: OutputPackage, *, allowed: bool, now: datetime, grace: timedelta,
                           current_publish_at: datetime | None = None) -> dict:
    """What the waiting YouTube video's status should be right now.

    Returns ``{"state": ..., "reason": ..., "changes": {...}}`` where state is
    ``scheduled`` (private + publishAt: YouTube publishes it), ``held`` (private;
    CLIPFORGE makes it live at the time — reason ``early`` — or publishing is on
    hold — reason ``on_hold``), ``live`` (make it public/unlisted now),
    ``missed`` or ``cancelled``. ``current_publish_at`` is the publishAt YouTube
    already has: a schedule YouTube holds is kept as its time approaches.
    """
    private = {"privacyStatus": "private", "publishAt": None}
    if package.status == "approved":  # "publish now" on a waiting video
        if not allowed:
            return {"state": "held", "reason": "on_hold", "changes": private}
        return {"state": "live", "reason": "now", "changes": {"privacyStatus": package.privacy or "public", "publishAt": None}}
    if package.status != "scheduled" or package.scheduled_at is None:
        return {"state": "cancelled", "reason": "cancelled", "changes": private}
    when = package.scheduled_at
    if not allowed:
        return {"state": "held", "reason": "on_hold", "changes": private}
    public = (package.privacy or "private") == "public"
    if public and (current_publish_at == when or when - now >= NATIVE_MIN_LEAD):
        return {"state": "scheduled", "reason": "youtube",
                "changes": {"privacyStatus": "private", "publishAt": when.isoformat().replace("+00:00", "Z")}}
    if now < when:
        return {"state": "held", "reason": "early", "changes": private}
    if now > when + grace:
        return {"state": "missed", "reason": "late", "changes": private}
    return {"state": "live", "reason": "due", "changes": {"privacyStatus": package.privacy or "public", "publishAt": None}}


def snippet_digest(package: OutputPackage) -> str:
    """Fingerprint of the text a waiting YouTube video should carry (edits are applied before it goes live)."""
    import hashlib
    import json

    return hashlib.sha256(json.dumps([package.title[:100], package.description[:5000], package.tags[:30]]).encode()).hexdigest()


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(UTC).replace(microsecond=0)


def _matches(remote: dict, changes: dict) -> bool:
    if remote.get("privacyStatus") != changes.get("privacyStatus"):
        return False
    return _parse_ts(remote.get("publishAt")) == _parse_ts(changes.get("publishAt"))


def sync_youtube(publication_id: uuid.UUID, *, now: datetime | None = None) -> dict:
    """Bring a waiting YouTube video in line with its package (schedule, hold, cancel, go live, confirm)."""
    from .service import confirm_published

    now = now or utcnow()
    with session_scope() as db:
        publication = db.get(Publication, publication_id)
        if publication is None or publication.status not in REMOTE_STATES or not publication.external_id:
            return {"skipped": True}
        package = db.get(OutputPackage, publication.package_id)
        account = db.get(SocialAccount, publication.account_id) if publication.account_id else None
        if account is None or account.status != "connected":
            publication.last_error = "The YouTube channel is disconnected; reconnect it to manage this scheduled video."
            return {"error": "not_connected"}
        publishing = app_settings.get_section(db, "publishing")
        allowed = publishing_allowed(db, "youtube")
        grace = timedelta(minutes=publishing.late_grace_minutes)
        token = google.access_token(db, account)
        video_id = publication.external_id
        applied = dict((publication.upload_state or {}).get("applied_status") or {})
        digest = snippet_digest(package)
        text_changed = (publication.upload_state or {}).get("snippet_digest") not in (None, digest)
        new_text = {"title": package.title[:100] or "Untitled", "description": package.description[:5000],
                    "tags": package.tags[:30]}
    auth = youtube.Auth(access_token=token)
    item = youtube.video_status(auth, video_id)
    with session_scope() as db:
        google.record_quota(db, "videos.list")
    if item is None:
        with session_scope() as db:
            publication = db.get(Publication, publication_id)
            publication.status = "failed"
            publication.last_error = "The video no longer exists on YouTube (deleted in YouTube Studio?)."
            package = db.get(OutputPackage, publication.package_id)
            if package.status == "scheduled":
                package.status = "failed"
            refresh_project_status(db, db.get(Clip, package.clip_id).project_id)
        return {"state": "gone"}
    if text_changed and (item.get("status") or {}).get("privacyStatus") != "public":
        # The owner edited the title/description/tags after the upload: the waiting video gets the new text.
        youtube.update_snippet(auth, video_id, item.get("snippet") or {}, new_text)
        with session_scope() as db:
            google.record_quota(db, "videos.update")
            publication = db.get(Publication, publication_id)
            publication.upload_state = {**(publication.upload_state or {}), "snippet_digest": digest}
    remote = item.get("status") or {}
    wanted_live = package.status == "approved" or (package.status == "scheduled" and package.scheduled_at is not None
                                                     and package.scheduled_at <= now)
    if remote.get("privacyStatus") == "public" or (
            wanted_live and remote.get("privacyStatus") == (package.privacy or "public") and not remote.get("publishAt")):
        confirm_published(publication_id, item=item, live_since=package.scheduled_at, now=now)
        return {"state": "published"}
    desired = desired_youtube_status(package, allowed=allowed, now=now, grace=grace,
                                     current_publish_at=_parse_ts(remote.get("publishAt")))
    changes = desired["changes"]
    if not _matches(remote, changes):
        youtube.update_status(auth, video_id, remote, changes)
        with session_scope() as db:
            google.record_quota(db, "videos.update")
        remote = {**remote, **{k: v for k, v in changes.items() if v is not None}}
        if changes.get("publishAt") is None:
            remote.pop("publishAt", None)
        applied = {"privacyStatus": changes.get("privacyStatus"), "publishAt": changes.get("publishAt"),
                   "at": now.isoformat()}
    state = desired["state"]
    if state == "live":
        confirm_published(publication_id, item={**item, "status": remote}, live_since=now, now=now)
        return {"state": "published"}
    with session_scope() as db:
        publication = db.get(Publication, publication_id)
        package = db.get(OutputPackage, publication.package_id)
        publication.upload_state = {**(publication.upload_state or {}), "applied_status": applied}
        if state == "scheduled":
            publication.status = "scheduled"
            publication.scheduled_at = package.scheduled_at
            # Long past its time but still private: YouTube did not publish it — say so, and stop waiting.
            if package.scheduled_at and now > package.scheduled_at + CONFIRM_TIMEOUT:
                publication.last_error = ("YouTube did not make this video public at its scheduled time; it is still "
                                          "private. Check it in YouTube Studio.")
                if now > package.scheduled_at + timedelta(hours=24):
                    publication.status = "failed"
                    package.status = "failed"
                    refresh_project_status(db, db.get(Clip, package.clip_id).project_id)
        elif state == "held":
            publication.status = "held"
            publication.scheduled_at = None
            if package.scheduled_at and package.scheduled_at > now:
                kick(db, at=package.scheduled_at)
        elif state == "missed":
            publication.status = "held"
            package.status = "missed"
            refresh_project_status(db, db.get(Clip, package.clip_id).project_id)
        elif state == "cancelled":
            publication.status = "cancelled"
            publication.scheduled_at = None
    return {"state": state, "reason": desired.get("reason")}


# ----------------------------------------------------------------- reconcile
def reconcile(*, now: datetime | None = None) -> dict:
    """Make reality match the plan: jobs for held posts, YouTube statuses, confirmations, missed posts."""
    now = now or utcnow()
    stats = {"jobs": 0, "synced": 0, "missed": 0, "errors": 0}
    with session_scope() as db:
        publishing = app_settings.get_section(db, "publishing")
        grace = timedelta(minutes=publishing.late_grace_minutes)
        emergency = app_settings.get_section(db, "control").emergency_stop
        for package in db.execute(select(OutputPackage).where(OutputPackage.status == "scheduled")).scalars().all():
            if remote_publication(db, package.id) is not None:
                continue
            when = package.scheduled_at
            if when is not None and now > when + grace and mode(package, now) != "youtube":
                if not active_jobs(db, package.id) or not publishing_allowed(db, package.platform):
                    package.status = "missed"
                    stats["missed"] += 1
                    refresh_project_status(db, db.get(Clip, package.clip_id).project_id)
                continue
            if emergency or not publishing_allowed(db, package.platform):
                continue
            if not active_jobs(db, package.id):
                ensure_job(db, package)
                stats["jobs"] += 1
        waiting = db.execute(select(Publication.id, Publication.status, Publication.scheduled_at,
                                    Publication.upload_state, Publication.package_id)
                             .where(Publication.status.in_(REMOTE_STATES), Publication.platform == "youtube")).all()
        due: list[uuid.UUID] = []
        for pub_id, status, pub_when, state, package_id in waiting:
            package = db.get(OutputPackage, package_id)
            applied = (state or {}).get("applied_status") or {}
            desired = desired_youtube_status(package, allowed=publishing_allowed(db, "youtube") and not emergency,
                                             now=now, grace=grace, current_publish_at=_parse_ts(applied.get("publishAt")))
            confirm_due = status == "scheduled" and pub_when is not None and now >= pub_when + timedelta(minutes=1)
            text_changed = (state or {}).get("snippet_digest") not in (None, snippet_digest(package))
            if confirm_due or text_changed or desired["state"] in ("live", "missed") \
                    or not _matches(applied, desired["changes"]):
                due.append(pub_id)
    for pub_id in due:
        try:
            sync_youtube(pub_id, now=now)
            stats["synced"] += 1
        except PlatformError as exc:
            stats["errors"] += 1
            log.warning("Could not update scheduled YouTube video for publication %s: %s", pub_id, exc)
            with session_scope() as db:
                publication = db.get(Publication, pub_id)
                publication.last_error = f"Could not reach YouTube to update the schedule: {exc}"[:1000]
    return stats
