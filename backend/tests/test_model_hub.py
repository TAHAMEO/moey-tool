"""Contract tests for the Model Hub against a local fake provider server.

These verify CLIPFORGE's own routing, fallback, budget, caching and accounting
logic using documented API shapes. Real provider connectivity is covered by
tests that run only when credentials are configured (see test_live_providers).
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from clipforge.db.models import AIUsage, ProviderConnection, ProviderModel
from clipforge.providers.adapters import AnthropicAdapter, GeminiAdapter
from clipforge.providers.base import ChatMessage, ChatParams, NoProviderAvailable
from clipforge.providers.hub import TaskSpec, hub, sync_models
from clipforge.security.crypto import store_secret
from clipforge.services import app_settings
from tests.fakes.provider_server import FakeProviderServer

pytestmark = pytest.mark.contract

MSG = [ChatMessage("user", "Classify: make 5 shorts from this video")]


def _conn(db, server, *, name, provider_type="openai_compatible", is_local=True, models=("fake-small-1b",),
          key="sk-test-key-123456", price=None):
    conn = ProviderConnection(provider_type=provider_type, name=name, base_url=f"{server.base}/v1", is_local=is_local)
    db.add(conn)
    db.flush()
    conn.secret_id = store_secret(db, f"provider:{conn.id}", key).id
    sync_models(db, conn, [(m, None, {}) for m in models])
    if price is not None:
        for m in conn.models:
            m.price_in_per_mtok, m.price_out_per_mtok, m.price_source = price[0], price[1], "owner"
    db.commit()
    return conn


def test_add_provider_via_api_discovers_and_tests(owner_client):
    with FakeProviderServer() as server:
        response = owner_client.post("/api/providers", json={"provider_type": "openai_compatible", "name": "Local LLM",
                                                             "base_url": f"{server.base}/v1"})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["test"]["ok"] is True and data["test"]["chat_ok"] is True
        assert data["connection"]["status"] == "ok" and data["connection"]["is_local"] is True
        assert {m["model_id"] for m in data["connection"]["models"]} == {"fake-small-1b", "fake-pro-70b"}
        listed = owner_client.get("/api/providers").json()["connections"][0]
        assert "api_key" not in listed and listed["key_hint"] is None


def test_api_key_is_never_returned(owner_client):
    with FakeProviderServer() as server:
        response = owner_client.post("/api/providers", json={"provider_type": "openai_compatible", "base_url": f"{server.base}/v1",
                                                             "api_key": "sk-supersecret-abcdefgh9876"})
        text = response.text + owner_client.get("/api/providers").text
        assert "supersecret" not in text and "sk-…9876" in text
        assert server.requests[-1]["headers"]["Authorization"] == "Bearer sk-supersecret-abcdefgh9876"


def test_local_first_routing_and_quality_escalation(db):
    with FakeProviderServer() as local, FakeProviderServer() as cloud:
        _conn(db, local, name="local", models=("llama-3.2-3b",))
        _conn(db, cloud, name="cloud", is_local=False, models=("gpt-5",), price=(1.25, 10.0))
        cheap = hub.complete(TaskSpec("intent", quality=1, max_tokens=50), MSG)
        assert cheap.connection_name == "local" and cheap.cost_usd == 0
        best = hub.complete(TaskSpec("knowledge", quality=3, max_tokens=50), MSG)
        assert best.connection_name == "cloud"
        assert best.cost_usd == pytest.approx((120 * 1.25 + 30 * 10.0) / 1e6)


def test_fallback_on_rate_limit_sets_cooldown(db):
    with FakeProviderServer() as bad, FakeProviderServer() as good:
        bad.behavior["mode"] = "rate_limit"
        a = _conn(db, bad, name="primary", models=("fake-pro-70b",))
        _conn(db, good, name="backup", models=("fake-small-1b",))
        result = hub.complete(TaskSpec("chat", quality=2), MSG)
        assert result.connection_name == "backup"
        assert [x["ok"] for x in result.attempts] == [False, True]
        db.expire_all()
        primary = db.get(ProviderConnection, a.id)
        assert primary.status == "degraded" and primary.cooldown_until is not None
        failures = db.execute(select(AIUsage).where(AIUsage.success.is_(False))).scalars().all()
        assert failures and failures[0].error_kind == "rate_limit"
        # While cooling down the failing provider is skipped entirely.
        again = hub.complete(TaskSpec("chat", quality=2), MSG)
        assert again.attempts == [{"provider": "backup", "model": "fake-small-1b", "ok": True}]


def test_invalid_credentials_marked(db):
    with FakeProviderServer() as server:
        server.behavior["mode"] = "auth"
        conn = _conn(db, server, name="expired")
        with pytest.raises(NoProviderAvailable):
            hub.complete(TaskSpec("chat"), MSG)
        db.expire_all()
        assert db.get(ProviderConnection, conn.id).status == "invalid_credentials"


def test_budget_blocks_paid_models(db):
    with FakeProviderServer() as cloud:
        _conn(db, cloud, name="cloud", is_local=False, models=("gpt-5",), price=(1.25, 10.0))
        app_settings.update_section(db, "ai", {"daily_budget_usd": 0.0})
        db.commit()
        with pytest.raises(NoProviderAvailable) as info:
            hub.complete(TaskSpec("chat"), MSG)
        assert "budget" in str(info.value)
        assert cloud.requests == []  # no money spent
        app_settings.update_section(db, "ai", {"daily_budget_usd": 5.0, "allow_paid_apis": False})
        db.commit()
        with pytest.raises(NoProviderAvailable) as info:
            hub.complete(TaskSpec("chat"), MSG)
        assert "paid APIs not allowed" in str(info.value)


def test_unknown_price_uses_conservative_estimate(db):
    with FakeProviderServer() as cloud:
        _conn(db, cloud, name="cloud", is_local=False, models=("mystery-model-x",))
        result = hub.complete(TaskSpec("chat", max_tokens=50), MSG)
        assert result.cost_estimated is True and result.cost_usd > 0


def test_response_cache_avoids_repeat_calls(db):
    with FakeProviderServer() as server:
        _conn(db, server, name="local")
        task = TaskSpec("intent", cache_ttl_s=3600, temperature=0.0)
        first = hub.complete(task, MSG)
        second = hub.complete(task, MSG)
        assert not first.cached and second.cached and second.text == first.text
        chat_calls = [r for r in server.requests if r["path"].endswith("/chat/completions")]
        assert len(chat_calls) == 1


class Intent(BaseModel):
    intent: str
    count: int


def test_structured_output_falls_back_on_invalid_json(db):
    with FakeProviderServer() as broken, FakeProviderServer() as good:
        broken.behavior["reply"] = "Sure! The intent is shorts."
        good.behavior["reply"] = '```json\n{"intent": "create_clips", "count": 5}\n```'
        _conn(db, broken, name="broken", models=("fake-pro-70b",))
        _conn(db, good, name="good", models=("fake-small-1b",))
        parsed, result = hub.complete_json(TaskSpec("intent", quality=2), MSG, Intent)
        assert parsed == Intent(intent="create_clips", count=5) and result.connection_name == "good"
        invalid = db.execute(select(AIUsage).where(AIUsage.error_kind == "invalid_output")).scalars().all()
        assert len(invalid) == 1


def test_no_provider_raises_for_local_fallback(db):
    with pytest.raises(NoProviderAvailable) as info:
        hub.complete(TaskSpec("chat"), MSG)
    assert "no AI provider is connected" in str(info.value)


def test_gemini_and_anthropic_adapter_shapes():
    with FakeProviderServer() as server:
        gem = GeminiAdapter(api_key="AIza-test", base_url=f"{server.base}/v1beta")
        out = gem.chat([ChatMessage("system", "be brief"), ChatMessage("user", "hi")], "gemini-2.5-flash", ChatParams(json_mode=True))
        assert out.text == "OK" and out.input_tokens == 100 and out.output_tokens == 20
        sent = server.requests[-1]
        assert sent["headers"]["x-goog-api-key"] == "AIza-test"
        assert sent["body"]["systemInstruction"]["parts"][0]["text"] == "be brief"
        assert sent["body"]["generationConfig"]["responseMimeType"] == "application/json"
        ant = AnthropicAdapter(api_key="sk-ant-test", base_url=f"{server.base}/v1")
        out = ant.chat([ChatMessage("system", "sys"), ChatMessage("user", "hi")], "claude-haiku-4-5", ChatParams())
        assert out.text == "OK" and out.input_tokens == 90
        sent = server.requests[-1]
        assert sent["headers"]["anthropic-version"] == "2023-06-01" and sent["body"]["system"] == "sys"
        assert [m.model_id for m in gem.list_models()] == ["gemini-2.5-flash"]


def test_model_profiles_inferred(db):
    with FakeProviderServer() as server:
        conn = _conn(db, server, name="cloud", is_local=False, models=("gpt-4o-mini", "gpt-5", "claude-haiku-4-5", "text-embedding-3-small"))
        models = {m.model_id: m for m in db.execute(select(ProviderModel).where(ProviderModel.connection_id == conn.id)).scalars()}
        assert models["gpt-4o-mini"].quality_tier == 1 and models["gpt-4o-mini"].price_in_per_mtok == 0.15
        assert models["gpt-5"].quality_tier == 3
        assert models["claude-haiku-4-5"].capabilities["vision"] is True
        assert models["text-embedding-3-small"].capabilities["chat"] is False


def test_ollama_native_chat_sizes_the_context_window():
    """Ollama defaults to a 4k context; long prompts (e.g. memory extraction) must request more."""
    from clipforge.providers.adapters import OllamaAdapter

    with FakeProviderServer() as server:
        ollama = OllamaAdapter(api_key=None, base_url=f"{server.base}/v1")
        out = ollama.chat([ChatMessage("user", "hi")], "llama3.2:1b", ChatParams(max_tokens=50))
        assert out.text == "OK" and out.input_tokens == 80 and out.output_tokens == 12
        sent = server.requests[-1]
        assert sent["path"] == "/api/chat" and sent["body"]["stream"] is False
        assert sent["body"]["options"]["num_ctx"] == 4096 and sent["body"]["options"]["num_predict"] == 50
        long_prompt = "word " * 6000  # ~30k characters, like Mem0's extraction prompt
        ollama.chat([ChatMessage("user", long_prompt)], "llama3.2:1b",
                    ChatParams(max_tokens=800, json_schema={"type": "object"}))
        sent = server.requests[-1]
        assert sent["body"]["options"]["num_ctx"] == 16384 and sent["body"]["format"] == {"type": "object"}
        assert [m.model_id for m in ollama.list_models()] == ["fake-small-1b", "fake-pro-70b"]
