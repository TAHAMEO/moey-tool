"""Source Access Resolver + workflow honesty (CONTRACT tests against fake platform APIs).

Real YouTube/Instagram endpoints are unreachable from the build sandbox and no
credentials were supplied, so these verify behaviour against documented API
shapes. See docs/TEST_RESULTS.md for what remains BLOCKED.
"""

from __future__ import annotations

import threading
import uuid
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from sqlalchemy import select

from clipforge.config import get_settings
from clipforge.db.base import session_scope, utcnow
from clipforge.db.models import Clip, Job, LiveSession, Project, Render, SocialAccount, Source
from clipforge.ingestion import acquire as acq
from clipforge.ingestion import resolver as rs
from clipforge.ingestion.urls import detect
from clipforge.jobs import queue
from clipforge.security.crypto import store_secret
from tests.fakes.platform_server import FakePlatforms

pytestmark = pytest.mark.contract


@pytest.fixture
def platforms(monkeypatch):
    with FakePlatforms() as fake:
        env = {
            "CLIPFORGE_GOOGLE_API_BASE": fake.base, "CLIPFORGE_GOOGLE_OAUTH_BASE": fake.base,
            "CLIPFORGE_YOUTUBE_OEMBED_URL": f"{fake.base}/oembed", "CLIPFORGE_YOUTUBE_ANALYTICS_BASE": f"{fake.base}/v2",
            "CLIPFORGE_INSTAGRAM_GRAPH_BASE": fake.base, "CLIPFORGE_GOOGLE_CLIENT_ID": "cid",
            "CLIPFORGE_GOOGLE_CLIENT_SECRET": "csecret", "CLIPFORGE_META_APP_ID": "123", "CLIPFORGE_META_APP_SECRET": "msecret",
        }
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        get_settings.cache_clear()
        yield fake
    get_settings.cache_clear()


def connect(platform: str, external_id: str) -> uuid.UUID:
    with session_scope() as db:
        account = SocialAccount(platform=platform, external_account_id=external_id, display_name=platform)
        db.add(account)
        db.flush()
        tokens = {"access_token": "tok-" + platform, "refresh_token": "ref", "expires_at": (utcnow() + timedelta(days=30)).isoformat()}
        account.secret_id = store_secret(db, f"oauth:{platform}:{account.id}", tokens).id
        return account.id


def make_source(url: str) -> uuid.UUID:
    info = detect(url)
    assert info.valid, info.error
    with session_scope() as db:
        source = Source(original_url=url, normalized_url=info.normalized_url, platform=info.platform,
                        source_type=info.source_type, external_id=info.external_id)
        db.add(source)
        db.flush()
        return source.id


def resolve(source_id) -> rs.AccessResult:
    with session_scope() as db:
        return rs.resolve(db, db.get(Source, source_id))


def test_youtube_third_party_video_is_metadata_only(platforms, monkeypatch):
    monkeypatch.setenv("CLIPFORGE_YOUTUBE_API_KEY", "AIza-test")
    get_settings.cache_clear()
    platforms.add_video("aaaaaaaaaaa", title="Someone else's talk", channel_id="UC_OTHER")
    res = resolve(make_source("https://www.youtube.com/watch?v=aaaaaaaaaaa"))
    assert res.state == rs.METADATA_ONLY and res.analysis_level == "metadata_only"
    assert res.capabilities == {"metadata": True, "transcript": False, "media": False, "live": False}
    assert res.metadata["title"] == "Someone else's talk"
    assert any("does not provide video downloads" in r for r in res.reasons)
    assert {a["action"] for a in res.alternatives} >= {"connect_youtube", "upload"}
    key_param = [r for r in platforms.requests if r["path"] == "/youtube/v3/videos"][0]["query"]
    assert key_param["key"] == "AIza-test"


def test_youtube_without_credentials_uses_oembed_and_detects_private(platforms):
    platforms.add_video("bbbbbbbbbbb", title="Public one")
    platforms.add_video("ccccccccccc", title="Private one", privacy="private")
    public = resolve(make_source("https://youtu.be/bbbbbbbbbbb"))
    assert public.state == rs.METADATA_ONLY and public.metadata["title"] == "Public one"
    private = resolve(make_source("https://youtu.be/ccccccccccc"))
    assert private.state == rs.SOURCE_PRIVATE and not private.capabilities["metadata"]


def test_owned_video_with_captions_is_transcript_only(platforms):
    connect("youtube", "UC_OWNER")
    platforms.add_video("ddddddddddd", title="My own talk", captions=True)
    platforms.captions["ddddddddddd"] = [{"id": "cap1", "snippet": {"language": "en", "trackKind": "standard"}}]
    res = resolve(make_source("https://www.youtube.com/watch?v=ddddddddddd"))
    assert res.owned is True and res.state == rs.TRANSCRIPT_ONLY and res.method == "youtube_owner_captions"
    assert res.capabilities["transcript"] and not res.capabilities["media"]
    assert any("can't analyse visuals" in r for r in res.reasons)


def test_owned_video_with_master_file_is_full_media(platforms):
    connect("youtube", "UC_OWNER")
    platforms.add_video("eeeeeeeeeee", title="Pricing Masterclass")
    masters = get_settings().library_dir / "masters"
    masters.mkdir(parents=True, exist_ok=True)
    (masters / "Pricing Masterclass.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    res = resolve(make_source("https://www.youtube.com/watch?v=eeeeeeeeeee"))
    assert res.state == rs.FULL_MEDIA_ACCESS and res.method == "owner_media_library"
    platforms.add_video("fffffffffff", title="Pricing Masterclass", ended=True)
    replay = resolve(make_source("https://www.youtube.com/live/fffffffffff"))
    assert replay.state == rs.REPLAY_ACCESSIBLE and replay.live_status == "ended"


def test_livestream_states(platforms):
    connect("youtube", "UC_OWNER")
    platforms.add_video("ggggggggggg", title="Live now", live="live")
    platforms.add_video("hhhhhhhhhhh", title="Tomorrow", live="upcoming")
    live_src = make_source("https://www.youtube.com/live/ggggggggggg")
    live = resolve(live_src)
    assert live.state == rs.METADATA_ONLY and live.live_status == "live" and live.acquire == {"await_live": True}
    assert any(a["action"] == "live_ingest" for a in live.alternatives)
    assert any("does not provide a live media feed" in r for r in live.reasons)
    scheduled = resolve(make_source("https://www.youtube.com/watch?v=hhhhhhhhhhh"))
    assert scheduled.live_status == "scheduled" and "waiting for the live source" in scheduled.reasons[0]
    with session_scope() as db:
        db.add(LiveSession(source_id=live_src, stream_key_hash=uuid.uuid4().hex, status="live"))
    authorized = resolve(live_src)
    assert authorized.state == rs.AUTHORIZED_LIVE_FEED and authorized.capabilities["live"]


def test_instagram_states_and_cdn_guard(platforms):
    not_connected = resolve(make_source("https://www.instagram.com/reel/AbCdEf12345/"))
    assert not_connected.state == rs.LOGIN_REQUIRED
    connect("instagram", platforms.ig_user_id)
    platforms.ig_media = [{"id": "1790001", "media_type": "VIDEO", "media_product_type": "REELS", "shortcode": "Mine12345",
                           "permalink": "https://www.instagram.com/reel/Mine12345/", "media_url": f"{platforms.base}/video.mp4",
                           "caption": "my reel"}]
    other = resolve(make_source("https://www.instagram.com/reel/Other12345/"))
    assert other.state == rs.PERMISSION_REQUIRED and any("other accounts" in r for r in other.reasons)
    mine_src = make_source("https://www.instagram.com/someuser/reel/Mine12345/")
    mine = resolve(mine_src)
    assert mine.state == rs.FULL_MEDIA_ACCESS and mine.method == "instagram_own_media" and mine.owned
    with session_scope() as db, pytest.raises(acq.AcquisitionError):
        # The fake media URL is not an Instagram CDN host: acquisition must refuse it.
        acq.acquire(db, db.get(Source, mine_src), mine.to_dict())


class _Direct(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, head_only):
        routes = {"/ok.mp4": (200, "video/mp4"), "/page": (200, "text/html"), "/gone.mp4": (404, "text/plain"),
                  "/secret.mp4": (403, "text/plain")}
        status, ctype = routes.get(self.path, (404, "text/plain"))
        body = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100 if status == 200 else b"x"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def do_HEAD(self):
        self._reply(True)

    def do_GET(self):
        self._reply(False)


def test_direct_link_states():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Direct)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        assert resolve(make_source(f"{base}/ok.mp4")).state == rs.FULL_MEDIA_ACCESS
        assert resolve(make_source(f"{base}/page")).state == rs.UNSUPPORTED_SOURCE
        assert resolve(make_source(f"{base}/gone.mp4")).state == rs.SOURCE_UNAVAILABLE
        assert resolve(make_source(f"{base}/secret.mp4")).state == rs.ACCESS_DENIED
        blocked = resolve(make_source("http://10.20.30.40/video.mp4"))
        assert blocked.state == rs.ACCESS_DENIED and "blocked" in blocked.reasons[0]
    finally:
        httpd.shutdown()


# ------------------------------------------------------- workflow honesty
def _run_workflow(source_id, request=None) -> tuple[Job, Project]:
    from clipforge.jobs.worker import Worker

    import clipforge.handlers  # noqa: F401

    with session_scope() as db:
        project = Project(source_id=source_id, title="t", request=request or {"count": 2, "min_seconds": 3, "max_seconds": 30,
                                                                             "aspects": ["9:16"], "platforms": ["youtube"]})
        db.add(project)
        db.flush()
        job = queue.enqueue(db, "workflow.video", {"project_id": str(project.id), "source_id": str(source_id)},
                            project_id=project.id)
        job_id, project_id = job.id, project.id
    Worker(["default", "media"], worker_id="wf-test").run(idle_exit_s=4)
    with session_scope() as db:
        job, project = db.get(Job, job_id), db.get(Project, project_id)
        db.expunge_all()
    return job, project


SRT = """1
00:00:00,000 --> 00:00:03,000
Here is the biggest mistake people make with pricing.

2
00:00:03,500 --> 00:00:07,000
They charge too little because they fear losing customers.

3
00:00:07,500 --> 00:00:11,000
Never compete on price. Compete on value instead.
"""


def test_transcript_only_workflow_never_claims_visual_analysis(platforms):
    connect("youtube", "UC_OWNER")
    platforms.add_video("iiiiiiiiiii", title="Owner talk", captions=True)
    platforms.captions["iiiiiiiiiii"] = [{"id": "capX", "snippet": {"language": "en", "trackKind": "standard"}}]
    platforms.caption_text["capX"] = SRT
    job, project = _run_workflow(make_source("https://www.youtube.com/watch?v=iiiiiiiiiii"))
    assert job.status == "completed" and job.status_label == "Completed (transcript only)"
    assert project.analysis_level == "transcript_only"
    assert "No visual analysis was performed" in project.limitations[0]
    with session_scope() as db:
        clips = db.execute(select(Clip).where(Clip.project_id == project.id)).scalars().all()
        assert clips and all(c.status == "candidate" and c.scoring_method == "transcript_only" for c in clips)
        assert db.execute(select(Render).join(Clip, Clip.id == Render.clip_id).where(Clip.project_id == project.id)).first() is None


def test_metadata_only_workflow_reports_access_required(platforms, monkeypatch):
    monkeypatch.setenv("CLIPFORGE_YOUTUBE_API_KEY", "AIza-test")
    get_settings.cache_clear()
    platforms.add_video("jjjjjjjjjjj", title="Not mine", channel_id="UC_OTHER")
    job, project = _run_workflow(make_source("https://www.youtube.com/watch?v=jjjjjjjjjjj"))
    assert job.status == "completed" and job.status_label == "Access required"
    assert project.status == "access_required" and project.analysis_level == "metadata_only"
    assert any("does not provide video downloads" in line for line in project.limitations)


def test_live_without_feed_waits_for_live_source(platforms):
    connect("youtube", "UC_OWNER")
    platforms.add_video("kkkkkkkkkkk", title="Going live", live="live")
    job, project = _run_workflow(make_source("https://www.youtube.com/live/kkkkkkkkkkk"))
    assert job.status == "waiting" and job.status_label == "Waiting for live source"
    assert project.status_label == "Waiting for live source"
    assert job.checkpoint["waiting"]["type"] == "live_wait"
