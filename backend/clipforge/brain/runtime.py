"""LangGraph runtime: durable checkpoints, child-job waits and approvals.

Every workflow job is a LangGraph thread (``job:<id>``) checkpointed in
PostgreSQL after each node. The worker resumes a thread in one of three ways:

* fresh thread           -> ``graph.invoke(initial_state)``
* crashed/paused mid-run -> ``graph.invoke(None)`` (continue from last checkpoint)
* waiting on interrupt   -> ``graph.invoke(Command(resume=value))``

Nodes delegate heavy work to specialist queues with :func:`await_children` and
ask for human decisions with :func:`await_approval`; both are LangGraph
``interrupt`` calls, so the Brain (the graph) keeps owning the execution state.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command, interrupt
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ..config import get_settings
from ..db.base import session_scope
from ..jobs import queue
from ..jobs.registry import JobContext, Suspend

log = logging.getLogger(__name__)

_lock = threading.Lock()
_pool: ConnectionPool | None = None
_saver: PostgresSaver | None = None
_contexts: dict[str, JobContext] = {}


def get_checkpointer() -> PostgresSaver:
    global _pool, _saver
    with _lock:
        if _saver is None:
            _pool = ConnectionPool(
                conninfo=get_settings().psycopg_dsn,
                min_size=1,
                max_size=8,
                kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
                open=True,
            )
            saver = PostgresSaver(_pool)
            saver.setup()
            _saver = saver
        return _saver


def reset_checkpointer() -> None:
    global _pool, _saver
    with _lock:
        if _pool is not None:
            _pool.close()
        _pool = None
        _saver = None


def thread_config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 200}


def job_context(config: dict[str, Any]) -> JobContext:
    """Inside a node: the JobContext of the job executing this thread."""
    return _contexts[config["configurable"]["thread_id"]]


@dataclass
class ChildSpec:
    key: str
    job_type: str
    payload: dict[str, Any]
    queue: str = "default"
    max_attempts: int = 3
    label: str = "Queued"


@dataclass
class ChildOutcome:
    status: str
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    job_id: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "completed"


def await_children(config: dict[str, Any], wait_group: str, children: list[ChildSpec], *, label: str) -> dict[str, ChildOutcome]:
    """Create child jobs (idempotently) and suspend until all have finished."""
    ctx = job_context(config)
    with session_scope() as db:
        for spec in children:
            queue.ensure_child(db, ctx.job_id, spec.key, spec.job_type, spec.payload, queue=spec.queue,
                               wait_group=wait_group, max_attempts=spec.max_attempts, status_label=spec.label)
    value = interrupt({"type": "children", "wait_group": wait_group, "label": label,
                       "children": [c.key for c in children]})
    outcomes = (value or {}).get("children", {})
    return {key: ChildOutcome(**{k: v for k, v in data.items() if k in ("status", "result", "error", "job_id")})
            for key, data in outcomes.items()}


def await_approval(payload: dict[str, Any], *, label: str = "Waiting for your approval") -> Any:
    return interrupt({"type": "approval", "label": label, **payload})


@dataclass
class GraphRun:
    values: dict[str, Any] = field(default_factory=dict)


def _pending_interrupts(snapshot) -> list[Any]:
    interrupts = list(getattr(snapshot, "interrupts", ()) or ())
    if not interrupts:
        for task in getattr(snapshot, "tasks", ()) or ():
            interrupts.extend(getattr(task, "interrupts", ()) or ())
    return interrupts


def run_graph_job(ctx: JobContext, compiled_graph, initial_state: dict[str, Any]) -> dict[str, Any]:
    """Execute/resume the job's LangGraph thread. Raises Suspend when waiting."""
    thread_id = f"job:{ctx.job_id}"
    config = thread_config(thread_id)
    _contexts[thread_id] = ctx
    try:
        snapshot = compiled_graph.get_state(config)
        pending = _pending_interrupts(snapshot)
        if pending and ctx.resume_value is not None:
            compiled_graph.invoke(Command(resume=ctx.resume_value), config)
        elif pending:
            value = getattr(pending[0], "value", {}) or {}
            raise Suspend(value.get("label", "Waiting"), value)
        elif snapshot.next:
            ctx.event("Resuming workflow from its last checkpoint", stage="recovery")
            compiled_graph.invoke(None, config)
        elif not snapshot.values:
            compiled_graph.invoke(initial_state, config)
        snapshot = compiled_graph.get_state(config)
        pending = _pending_interrupts(snapshot)
        if pending:
            value = getattr(pending[0], "value", {}) or {}
            raise Suspend(value.get("label", "Waiting"), value)
        return dict(snapshot.values)
    finally:
        _contexts.pop(thread_id, None)


def new_thread_id(prefix: str) -> str:
    return f"{prefix}:{uuid.uuid4()}"
