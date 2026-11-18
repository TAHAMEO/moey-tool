"""Remember instantly, refine with an LLM in the background."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from clipforge.db.base import session_scope
from clipforge.db.models import Job
from clipforge.memory.service import get_memory


@pytest.fixture
def memory():
    m = get_memory()
    m.delete_all(user_id="refine-test")
    yield m
    m.delete_all(user_id="refine-test")


def _verbatim(memory, text="My audience is mostly coffee shop owners"):
    events = memory.remember(text, user_id="refine-test", infer=False)
    return events[0]["id"]


def test_new_facts_replace_the_verbatim_copy(memory, monkeypatch):
    verbatim = _verbatim(memory)
    real_add = memory._memory.add

    def fake_infer_add(messages, *, metadata, infer, **scope):  # what Mem0 returns when the LLM extracts a fact
        assert infer is True
        out = real_add([{"role": "user", "content": "Audience: independent coffee shop owners"}], metadata=metadata,
                       infer=False, **scope)
        return {"results": [{**e, "event": "ADD"} for e in out["results"]]}

    monkeypatch.setattr(memory._memory, "add", fake_infer_add)
    memory.refine("My audience is mostly coffee shop owners", verbatim_id=verbatim, user_id="refine-test")
    texts = [m["memory"] for m in memory.list(user_id="refine-test")]
    assert texts == ["Audience: independent coffee shop owners"]


@pytest.mark.parametrize("event", ["UPDATE", "NONE"])
def test_verbatim_kept_when_updated_in_place_or_nothing_new(memory, monkeypatch, event):
    verbatim = _verbatim(memory)
    monkeypatch.setattr(memory._memory, "add", lambda *a, **k: {"results": [{"id": verbatim, "event": event,
                                                                             "memory": "x"}]})
    memory.refine("My audience is mostly coffee shop owners", verbatim_id=verbatim, user_id="refine-test")
    assert [m["id"] for m in memory.list(user_id="refine-test")] == [verbatim]


def test_remember_is_instant_and_queues_refinement(owner_client, monkeypatch, memory):
    from clipforge.memory.service import MemoryService

    monkeypatch.setattr(MemoryService, "llm_available", staticmethod(lambda: True))
    res = owner_client.post("/api/memory", json={"text": "Never use profanity in captions"})
    assert res.status_code == 200 and res.json()[0]["event"] == "ADD"
    with session_scope() as db:
        job = db.execute(select(Job).where(Job.type == "memory.refine")).scalar_one()
        assert job.payload["verbatim_id"] == res.json()[0]["id"] and job.status == "queued"
