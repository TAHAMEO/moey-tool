"""Remember instantly; let an LLM refine the memory in the background.

Fact extraction by a model can take a long time (about 90 s for a 1B model on a CPU),
so the chat and the API store the cleaned text right away and queue a refinement job.
"""

from __future__ import annotations

from typing import Any

from ..db.base import session_scope
from ..jobs import queue
from .service import get_memory


def remember_then_refine(text: str, *, user_id: str, category: str) -> list[dict[str, Any]]:
    memory = get_memory()
    events = memory.remember(text, user_id=user_id, category=category, infer=False)
    verbatim = next((e.get("id") for e in events if e.get("event") == "ADD" and e.get("id")), None)
    if verbatim and memory.llm_available():
        with session_scope() as db:
            queue.enqueue(db, "memory.refine", {"text": text, "user_id": user_id, "category": category,
                                                "verbatim_id": verbatim},
                          dedupe_key=f"memory-refine:{verbatim}", status_label="Refining memory",
                          priority=200)  # after orchestration and publishing work
    return events
