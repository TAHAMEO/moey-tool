"""Brain chat, memory persistence across restarts, knowledge engine, job/memory APIs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select

from clipforge.db.base import session_scope
from clipforge.db.models import Job, KnowledgeItem, Project, Source
from clipforge.providers.hub import sync_models
from tests.fakes.provider_server import FakeProviderServer

BACKEND = Path(__file__).resolve().parents[1]


def _conversation(client) -> str:
    return client.post("/api/chat/conversations", json={}).json()["id"]


def _say(client, conv, text, **extra):
    response = client.post(f"/api/chat/conversations/{conv}/messages", json={"text": text, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def test_link_creates_project_and_job(owner_client):
    conv = _conversation(owner_client)
    reply = _say(owner_client, conv, "Create 5 Shorts from this YouTube video: https://youtu.be/dQw4w9WgXcQ")
    assert reply["meta"]["intent"] == "process_source"
    assert "On it" in reply["content"] and "5 clip(s)" in reply["content"]
    cards = [c for c in reply["meta"]["cards"] if c["type"] == "job"]
    assert len(cards) == 1
    with session_scope() as db:
        job = db.get(Job, cards[0]["job_id"])
        project = db.get(Project, cards[0]["project_id"])
        assert job.type == "workflow.video" and job.status == "queued"
        assert project.request["count"] == 5 and project.request["platforms"] == ["youtube"]
        source = db.get(Source, project.source_id)
        assert source.platform == "youtube" and source.external_id == "dQw4w9WgXcQ"
    history = owner_client.get(f"/api/chat/conversations/{conv}/messages").json()
    assert [m["role"] for m in history] == ["user", "assistant"]


def test_same_source_in_any_url_form_is_deduplicated(owner_client):
    conv = _conversation(owner_client)
    _say(owner_client, conv, "make clips https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s")
    second = _say(owner_client, conv, "make clips https://youtu.be/dQw4w9WgXcQ")
    assert "seen this source before" in second["content"]
    with session_scope() as db:
        assert db.execute(select(func.count()).select_from(Source)).scalar_one() == 1
        assert db.execute(select(func.count()).select_from(Project)).scalar_one() == 2


def test_invalid_link_explains_limitation(owner_client):
    conv = _conversation(owner_client)
    reply = _say(owner_client, conv, "clip this https://www.youtube.com/@somechannel")
    assert "channel, playlist or page link" in reply["content"]
    with session_scope() as db:
        assert db.execute(select(func.count()).select_from(Project)).scalar_one() == 0


def test_viewer_cannot_start_production(owner_client, client):
    owner_client.post("/api/auth/users", json={"email": "v@example.com", "password": "Viewer-Pass-77", "role": "viewer"})
    owner_client.post("/api/auth/logout")
    login = client.post("/api/auth/login", json={"email": "v@example.com", "password": "Viewer-Pass-77"})
    client.headers["x-csrf-token"] = login.json()["csrf_token"]
    conv = _conversation(client)
    reply = _say(client, conv, "make 3 shorts https://youtu.be/dQw4w9WgXcQ")
    assert "cannot do that" in reply["content"]
    with session_scope() as db:
        assert db.execute(select(func.count()).select_from(Job)).scalar_one() == 0


def test_memory_survives_process_restart(owner_client):
    conv = _conversation(owner_client)
    reply = _say(owner_client, conv, "Remember that I prefer bold yellow captions and Shorts under 40 seconds")
    assert reply["meta"]["intent"] == "remember" and "permanent memory" in reply["content"]
    suggestion = [c for c in reply["meta"]["cards"] if c["type"] == "settings_suggestion"]
    assert suggestion and suggestion[0]["patch"]["max_seconds"] == 40
    user_id = owner_client.get("/api/auth/me").json()["id"]
    # A brand-new Python process (fresh interpreter, fresh Mem0 instance) must find it.
    code = (
        "import json; from clipforge.memory.service import get_memory; "
        f"print(json.dumps(get_memory().recall('what caption style does the owner like', user_id='{user_id}', limit=1)))"
    )
    env = {**os.environ, "MEM0_TELEMETRY": "False"}
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    found = json.loads(out.stdout.strip().splitlines()[-1])
    assert found and "yellow captions" in found[0]["memory"]


def test_conversation_state_is_checkpointed(owner_client):
    conv = _conversation(owner_client)
    _say(owner_client, conv, "hello")
    _say(owner_client, conv, "what's the status?")
    code = (
        "from clipforge.brain.chat import get_graph; from clipforge.brain.runtime import thread_config; "
        f"s = get_graph().get_state(thread_config('chat:{conv}')); print(len(s.values.get('turns', [])))"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env={**os.environ, "MEM0_TELEMETRY": "False"},
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-1500:]
    assert out.stdout.strip().splitlines()[-1] == "4"


def test_question_without_model_is_honest(owner_client):
    conv = _conversation(owner_client)
    reply = _say(owner_client, conv, "What is the ideal length for a YouTube Short?")
    assert reply["meta"]["intent"] == "question"
    assert "don't have an AI model connected" in reply["content"]


def _local_provider(server, name="local-llm"):
    from clipforge.db.models import ProviderConnection

    with session_scope() as db:
        conn = ProviderConnection(provider_type="openai_compatible", name=name, base_url=f"{server.base}/v1", is_local=True)
        db.add(conn)
        db.flush()
        sync_models(db, conn, [("fake-pro-70b", None, {})])


@pytest.mark.contract
def test_knowledge_saved_then_reused(owner_client):
    with FakeProviderServer() as server:
        server.behavior["reply"] = json.dumps({"answer": "Most successful Shorts are 20-45 seconds long.",
                                               "claims": ["Shorts can be up to 3 minutes"], "confidence": 0.7, "caveats": ""})
        _local_provider(server)
        conv = _conversation(owner_client)
        first = _say(owner_client, conv, "What length works best for YouTube Shorts?")
        assert "20-45 seconds" in first["content"] and "single source" in first["content"]
        calls = len([r for r in server.requests if r["path"].endswith("/chat/completions")])
        second = _say(owner_client, conv, "What length works best for YouTube Shorts?")
        assert "(from memory)" in second["content"]
        assert len([r for r in server.requests if r["path"].endswith("/chat/completions")]) == calls
    with session_scope() as db:
        item = db.execute(select(KnowledgeItem)).scalar_one()
        assert item.verification_status == "single_source" and item.reuse_count == 1
        assert item.responses[0]["provider"] == "local-llm"


@pytest.mark.contract
def test_knowledge_consensus_and_disagreement(owner_client):
    with FakeProviderServer() as a, FakeProviderServer() as b:
        a.behavior["reply"] = json.dumps({"answer": "Post Reels in the early evening when your audience is active.",
                                          "claims": ["Evening posts get more reach"], "confidence": 0.8})
        b.behavior["reply"] = json.dumps({"answer": "Reels posted in the early evening tend to reach more of your audience.",
                                          "claims": ["Evening posts reach more people", "Hashtags no longer matter at all"],
                                          "confidence": 0.8})
        _local_provider(a, "model-a")
        _local_provider(b, "model-b")
        result = owner_client.post("/api/knowledge/ask", json={"question": "When should I post Reels?", "important": True})
        assert result.status_code == 200, result.text
        data = result.json()
        assert sorted(p.split("/")[0] for p in data["providers"]) == ["model-a", "model-b"]
        assert data["verification_status"] in ("consensus", "disputed")
        assert any("Hashtags" in d for d in data["disagreements"])  # unsupported claim surfaced
    listed = owner_client.get("/api/knowledge").json()
    assert listed[0]["agreement"] is not None
    verify = owner_client.patch(f"/api/knowledge/{listed[0]['id']}", json={"status": "verified", "note": "checked"})
    assert verify.json()["verification_status"] == "verified"


def test_emergency_stop_via_chat(owner_client):
    conv = _conversation(owner_client)
    _say(owner_client, conv, "make clips https://youtu.be/dQw4w9WgXcQ")
    reply = _say(owner_client, conv, "emergency stop")
    assert "Emergency stop is ON" in reply["content"]
    with session_scope() as db:
        statuses = {j.status for j in db.execute(select(Job)).scalars()}
    assert statuses == {"cancelled"}
    assert owner_client.get("/api/settings").json()["control"]["emergency_stop"] is True


def test_status_and_costs_intents(owner_client):
    conv = _conversation(owner_client)
    _say(owner_client, conv, "make clips https://youtu.be/dQw4w9WgXcQ")
    status = _say(owner_client, conv, "how's it going?")
    assert "working on" in status["content"]
    costs = _say(owner_client, conv, "how much have we spent?")
    assert "$0.0000 today" in costs["content"]


def test_jobs_api_cancel_and_inspect(owner_client):
    conv = _conversation(owner_client)
    reply = _say(owner_client, conv, "make clips https://youtu.be/dQw4w9WgXcQ")
    job_id = reply["meta"]["cards"][0]["job_id"]
    detail = owner_client.get(f"/api/jobs/{job_id}").json()
    assert detail["status"] == "queued" and detail["events"]
    assert owner_client.post(f"/api/jobs/{job_id}/cancel").json()["status"] == "cancelled"
    assert owner_client.post(f"/api/jobs/{job_id}/retry").json()["status"] == "queued"
    assert owner_client.post(f"/api/jobs/{job_id}/approve", json={"approved": []}).status_code == 409


def test_memory_api_crud_and_export(owner_client):
    added = owner_client.post("/api/memory", json={"text": "Our audience is mostly small business owners"}).json()
    memory_id = added[0]["id"]
    assert any(m["id"] == memory_id for m in owner_client.get("/api/memory").json())
    owner_client.patch(f"/api/memory/{memory_id}", json={"text": "Our audience is mostly freelance designers"})
    found = owner_client.get("/api/memory/search", params={"q": "who is the audience"}).json()
    assert "freelance designers" in found[0]["memory"]
    export = owner_client.get("/api/memory-export")
    assert export.headers["content-disposition"].startswith("attachment") and export.json()["user_memories"]
    assert owner_client.delete(f"/api/memory/{memory_id}").json() == {"ok": True}
    assert owner_client.get("/api/memory").json() == []


def test_upload_validation_and_dedupe(owner_client, tmp_path):
    fake = tmp_path / "notes.mp4"
    fake.write_bytes(b"this is not a video at all" * 10)
    bad = owner_client.post("/api/chat/upload", files={"file": ("notes.mp4", fake.read_bytes(), "video/mp4")})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "not_media"
    clip = tmp_path / "tiny.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10",
                    "-f", "lavfi", "-i", "sine=frequency=300", "-t", "1", "-c:v", "libx264", "-c:a", "aac", str(clip)], check=True)
    first = owner_client.post("/api/chat/upload", files={"file": ("tiny.mp4", clip.read_bytes(), "video/mp4")}).json()
    second = owner_client.post("/api/chat/upload", files={"file": ("tiny.mp4", clip.read_bytes(), "video/mp4")}).json()
    assert first["asset_id"] == second["asset_id"] and second["duplicate"] is True
    exe = owner_client.post("/api/chat/upload", files={"file": ("run.exe", b"MZ" * 100, "application/octet-stream")})
    assert exe.status_code == 400
    conv = _conversation(owner_client)
    reply = _say(owner_client, conv, "make 2 shorts from this", attachments=[first["asset_id"]])
    assert reply["meta"]["intent"] == "process_source" and reply["meta"]["cards"]
