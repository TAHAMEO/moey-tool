"""Background refinement of memories with an LLM (see clipforge.memory.tasks)."""

from __future__ import annotations

from ..jobs.registry import JobContext, RetryableError, handler
from ..memory.service import get_memory


@handler("memory.refine", queue="default", needs_secrets=True)
def refine_memory(ctx: JobContext) -> dict:
    from mem0.exceptions import LLMError

    p = ctx.payload
    try:
        events = get_memory().refine(p["text"], verbatim_id=p["verbatim_id"], user_id=p["user_id"],
                                     category=p.get("category", "fact"))
    except LLMError as exc:  # model busy, timed out or unavailable: the verbatim memory stays; try later
        raise RetryableError(f"Memory refinement postponed: {exc}") from exc
    return {"events": [{"event": e.get("event"), "memory": (e.get("memory") or "")[:200]} for e in events]}
