"""Brain Chat's assistant (local AI): improving clips and controlling CLIPFORGE from plain language,
and setting up local AI on this PC (detect Ollama, connect it, download a model with progress)."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from clipforge.brain import assistant
from clipforge.config import get_settings
from clipforge.db.base import session_scope
from clipforge.db.models import Clip, Job, MediaAsset, OutputPackage, Project, ProviderConnection, Source
from clipforge.providers import local_ai
from clipforge.storage import get_storage
from tests.fakes.provider_server import FakeProviderServer

FACTS = {"project": {"id": None, "title": "Talk", "status": "Clips ready"},
         "clips": [{"rank": n, "length": 30.0, "weakest": [], "qc_failed": []} for n in (1, 2, 3)]}


def _steps(text: str) -> list[dict]:
    plan = assistant.local_plan(text, FACTS)
    return [s.model_dump(exclude_none=True) for s in plan.steps] if plan else []


def test_simple_requests_work_without_any_model():
    assert _steps("make clip 2 5 seconds shorter") == [{"action": "edit_clip", "clip": 2, "trim_end_s": 5.0}]
    assert _steps("cut the first 3 seconds of clip 1") == [{"action": "edit_clip", "clip": 1, "trim_start_s": 3.0}]
    assert _steps("make the third clip longer by four seconds") == [{"action": "edit_clip", "clip": 3, "trim_end_s": -4.0}]
    assert _steps("use karaoke captions on all clips") == [{"action": "edit_clip", "clip": 0, "caption_style": "karaoke_fill"}]
    assert _steps('rename clip 3 to "My best pricing tip"') == [
        {"action": "set_title", "clip": 3, "title": "My best pricing tip", "platform": "all"}]
    assert _steps("re-render clip 2") == [{"action": "rerender", "clip": 2}]
    assert _steps("remove the captions from clip 1") == [{"action": "edit_clip", "clip": 1, "burn_captions": False}]
    assert _steps("make clip 2 shorter") == []  # how much shorter needs a model (or a number)


def _project_with_clips(tmp_path, n=2) -> dict:
    """A processed project: source, a stored prepared video, clips and their packages."""
    with session_scope() as db:
        source = Source(platform="upload", source_type="file", external_id="t", original_url="upload://t",
                        normalized_url="upload://t")
        db.add(source)
        db.flush()
        mez = tmp_path / "mez.mp4"
        mez.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\0" * 64)
        key = f"sources/{source.id}/mezzanine.mp4"
        get_storage().put_file(mez, key)
        db.add(MediaAsset(kind="mezzanine", storage_key=key, source_id=source.id, mime="video/mp4", size_bytes=80,
                          duration_s=120.0, width=1280, height=720, info={}))
        project = Project(title="Pricing talk", source_id=source.id, request={}, status="ready", status_label="Clips ready")
        db.add(project)
        db.flush()
        ids = []
        for rank in range(1, n + 1):
            clip = Clip(project_id=project.id, source_id=source.id, rank=rank, start_s=10.0 * rank, end_s=10.0 * rank + 30,
                        title=f"Clip {rank}", transcript_text="Here is the biggest mistake", scores={"hook": 0.3, "clarity": 0.8},
                        total_score=0.6, status="rendered")
            db.add(clip)
            db.flush()
            db.add(OutputPackage(clip_id=clip.id, platform="youtube", title=f"Clip {rank} #shorts", status="ready"))
            ids.append(str(clip.id))
        return {"project_id": str(project.id), "clip_ids": ids}


def test_plan_is_carried_out_with_the_apps_own_rules(owner_client, tmp_path):
    made = _project_with_clips(tmp_path)
    with session_scope() as db:
        facts = assistant.project_facts(db, db.get(Project, uuid.UUID(made["project_id"])))
        owner = owner_client.get("/api/auth/me").json()
    plan = assistant.Plan(reply="", steps=[
        assistant.Step(action="edit_clip", clip=2, trim_start_s=2, trim_end_s=5, caption_style="karaoke_fill"),
        assistant.Step(action="set_title", clip=1, title="Stop undercharging", platform="all"),
        assistant.Step(action="edit_clip", clip=9, trim_end_s=1),
        assistant.Step(action="change_default", setting="max_seconds", value="45"),
    ])
    outcome = assistant.execute(plan, facts, user_id=owner["id"], role="owner")
    assert [d["step"] for d in outcome["done"]] == ["Clip 2", "Clip 1", "Suggested a default"]
    assert "There is no clip 9 in this project." in outcome["problems"]
    assert {"type": "settings_suggestion", "section": "clips", "patch": {"max_seconds": 45.0}} in outcome["cards"]
    with session_scope() as db:
        two = db.get(Clip, uuid.UUID(made["clip_ids"][1]))
        assert (two.start_s, two.end_s) == (22.0, 45.0)
        job = db.execute(select(Job).where(Job.type == "media.render")).scalar_one()
        assert job.payload == {"clip_id": made["clip_ids"][1], "options": {"caption_style": "karaoke_fill"}}
        one = db.get(Clip, uuid.UUID(made["clip_ids"][0]))
        package = db.execute(select(OutputPackage).where(OutputPackage.clip_id == one.id)).scalar_one()
        assert one.title == "Stop undercharging" and package.title == "Stop undercharging #shorts"
    # A viewer may ask, but not change clips.
    viewer = assistant.execute(assistant.Plan(steps=[assistant.Step(action="rerender", clip=1)]), facts,
                               user_id=owner["id"], role="viewer")
    assert viewer["done"] == [] and "Only an editor" in viewer["problems"][0]


def test_chat_without_a_model_does_simple_edits_and_says_how_to_get_local_ai(owner_client, tmp_path):
    _project_with_clips(tmp_path)
    conv = owner_client.post("/api/chat/conversations", json={}).json()["id"]
    reply = owner_client.post(f"/api/chat/conversations/{conv}/messages",
                              json={"text": "Make clip 1 3 seconds shorter"}).json()
    assert reply["meta"]["intent"] == "edit"
    assert "Clip 1" in reply["content"] and "cut 3 s at the end" in reply["content"]
    vague = owner_client.post(f"/api/chat/conversations/{conv}/messages",
                              json={"text": "How can I improve these clips?"}).json()
    assert "hook is weak" in vague["content"] and "Local AI on this PC" in vague["content"]


@pytest.mark.contract
def test_chat_with_local_model_plans_and_acts(owner_client, tmp_path):
    made = _project_with_clips(tmp_path)
    with FakeProviderServer() as ollama:
        def reply(body):
            prompt = body["messages"][-1]["content"]
            assert "Clip 2:" in prompt and "<untrusted_clip_title>" in prompt  # the clips, fenced as data
            return json.dumps({"reply": "Clip 2 now starts on the strongest line and uses karaoke captions.",
                               "steps": [{"action": "edit_clip", "clip": 2, "trim_start_s": 4, "caption_style": "karaoke_fill"},
                                         {"action": "explode", "clip": 1}]})  # an unknown step is ignored

        ollama.behavior.update(reply=reply, models=["qwen2.5:3b"])
        res = owner_client.post("/api/providers", json={"provider_type": "ollama", "base_url": f"{ollama.base}/v1"})
        assert res.status_code == 200, res.text
        conv = owner_client.post("/api/chat/conversations", json={}).json()["id"]
        answer = owner_client.post(f"/api/chat/conversations/{conv}/messages",
                                   json={"text": "The second clip starts too slowly, fix it and make captions karaoke"}).json()
    assert answer["meta"]["intent"] == "edit"
    assert answer["content"].startswith("Clip 2 now starts on the strongest line")
    assert any(a["step"] == "Understood by" and "qwen2.5:3b" in a["detail"] for a in answer["meta"]["actions"])
    with session_scope() as db:
        clip = db.get(Clip, uuid.UUID(made["clip_ids"][1]))
        assert clip.start_s == 24.0
        assert db.execute(select(Job).where(Job.type == "media.render")).scalar_one()


# ------------------------------------------------------------------ local AI setup
@pytest.mark.contract
def test_detect_connect_and_download_a_model(owner_client, monkeypatch):
    with FakeProviderServer() as ollama:
        ollama.behavior["models"] = ["llama3.2:3b"]
        monkeypatch.setattr(get_settings(), "ollama_url", ollama.base)
        status = owner_client.get("/api/providers/local").json()
        found = next(s for s in status["servers"] if s["kind"] == "ollama")
        assert found["running"] and found["version"] == "0.12.0-fake" and found["models"][0]["name"] == "llama3.2:3b"
        assert found["connection"] is None and status["recommended"][0]["name"] == "qwen2.5:3b"

        connected = owner_client.post("/api/providers/local/connect", json={"kind": "ollama"})
        assert connected.status_code == 200, connected.text
        assert connected.json()["test"]["ok"] and connected.json()["models"] == ["llama3.2:3b"]
        again = owner_client.post("/api/providers/local/connect", json={"kind": "ollama"}).json()
        assert again["connection_id"] == connected.json()["connection_id"]  # one connection, refreshed

        bad = owner_client.post("/api/providers/local/pull", json={"model": "../../etc"})
        assert bad.status_code == 400
        job = owner_client.post("/api/providers/local/pull", json={"model": "qwen2.5:3b"}).json()
        import clipforge.handlers  # noqa: F401 - registers the job handlers, as `clipforge worker` does
        from clipforge.jobs.worker import Worker

        Worker(["default"], worker_id="test-ai").run(idle_exit_s=3)
        done = owner_client.get(f"/api/jobs/{job['id']}").json()
        assert done["status"] == "completed", (done["error"], done["events"][-3:])
        assert done["result"]["model"] == "qwen2.5:3b" and "qwen2.5:3b" in done["result"]["models"]
        pulls = [r for r in ollama.requests if r["path"] == "/api/pull"]
        assert pulls and pulls[0]["body"] == {"model": "qwen2.5:3b", "stream": True}
    with session_scope() as db:
        assert len(db.execute(select(ProviderConnection)).scalars().all()) == 1


def test_pull_reports_bytes_and_errors(monkeypatch):
    with FakeProviderServer() as ollama:
        monkeypatch.setattr(get_settings(), "ollama_url", ollama.base)
        seen = []
        local_ai.pull("qwen2.5:3b", on_progress=lambda done, total, status: seen.append((done, total, status)))
        assert seen[-1] == (4_000_000, 4_000_000, "success")
        ollama.behavior["pull_error"] = "pull model manifest: file does not exist"
        with pytest.raises(local_ai.LocalAIError, match="file does not exist"):
            local_ai.pull("nope:1b")
    with pytest.raises(local_ai.LocalAIError, match="not running"):
        local_ai.pull("qwen2.5:3b")  # the fake server is gone: nothing listens there now


def test_auto_connect_only_when_needed(monkeypatch):
    with FakeProviderServer() as ollama:
        ollama.behavior["models"] = ["qwen2.5:3b"]
        monkeypatch.setattr(get_settings(), "ollama_url", ollama.base)
        monkeypatch.setattr(get_settings(), "local_ai_autoconnect", True)
        local_ai._last_auto[0] = 0.0
        with session_scope() as db:
            assert local_ai.auto_connect(db) is True
            assert local_ai.chat_model_ready(db)
        local_ai._last_auto[0] = 0.0
        with session_scope() as db:
            assert local_ai.auto_connect(db) is False  # already connected: nothing to do


def test_no_proxy_for_this_pc():
    from clipforge.providers.adapters import _direct

    assert _direct("http://127.0.0.1:11434/v1/chat") and _direct("http://localhost:1234/v1")
    assert not _direct("https://api.openai.com/v1/chat/completions")
