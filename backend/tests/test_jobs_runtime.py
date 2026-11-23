"""Durable jobs + LangGraph runtime: real Postgres, real worker loop, real checkpoints."""

from __future__ import annotations

import operator
import threading
from datetime import timedelta
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select

from clipforge.brain.runtime import ChildSpec, await_approval, await_children, get_checkpointer, run_graph_job
from clipforge.db.base import session_scope, utcnow
from clipforge.db.models import Job, JobEvent
from clipforge.jobs import queue
from clipforge.jobs.registry import PermanentError, RetryableError, handler
from clipforge.jobs.worker import Worker

COUNTS: dict[str, int] = {}
FAIL_ONCE: set[str] = set()


class WFState(TypedDict, total=False):
    log: Annotated[list[str], operator.add]
    doubled: dict
    approved: bool


def node_a(state: WFState, config) -> WFState:
    COUNTS["a"] = COUNTS.get("a", 0) + 1
    return {"log": ["a"]}


def node_b(state: WFState, config) -> WFState:
    COUNTS["b"] = COUNTS.get("b", 0) + 1
    if "crash_b" in FAIL_ONCE:
        FAIL_ONCE.discard("crash_b")
        raise ValueError("simulated crash in node b")
    return {"log": ["b"]}


def node_delegate(state: WFState, config) -> WFState:
    outcomes = await_children(config, "double", [
        ChildSpec(key=f"n{n}", job_type="test.child", payload={"n": n}, queue="media") for n in (2, 5)
    ], label="Processing in parallel")
    return {"doubled": {k: v.result["value"] for k, v in outcomes.items() if v.ok}, "log": ["delegate"]}


def node_approve(state: WFState, config) -> WFState:
    decision = await_approval({"what": "publish"})
    return {"approved": bool(decision.get("approved")), "log": ["approve"]}


def _graph():
    g = StateGraph(WFState)
    g.add_node("a", node_a)
    g.add_node("b", node_b)
    g.add_node("delegate", node_delegate)
    g.add_node("approve", node_approve)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", "delegate")
    g.add_edge("delegate", "approve")
    g.add_edge("approve", END)
    return g.compile(checkpointer=get_checkpointer())


@handler("test.workflow")
def _wf(ctx):
    values = run_graph_job(ctx, _graph(), {"log": []})
    return {"log": values["log"], "doubled": values.get("doubled"), "approved": values.get("approved")}


@handler("test.child", queue="media")
def _child(ctx):
    key = f"child-{ctx.payload['n']}"
    if key in FAIL_ONCE:
        FAIL_ONCE.discard(key)
        raise RetryableError("transient glitch")
    return {"value": ctx.payload["n"] * 2}


def node_glitch(state: WFState, config) -> WFState:
    if "glitch" in FAIL_ONCE:
        FAIL_ONCE.discard("glitch")
        raise RetryableError("network blip after several stages")
    return {"log": ["glitch"]}


@handler("test.long_workflow")
def _long_wf(ctx):
    g = StateGraph(WFState)
    g.add_node("delegate", node_delegate)
    g.add_node("approve", node_approve)
    g.add_node("glitch", node_glitch)
    g.add_edge(START, "delegate")
    g.add_edge("delegate", "approve")
    g.add_edge("approve", "glitch")
    g.add_edge("glitch", END)
    values = run_graph_job(ctx, g.compile(checkpointer=get_checkpointer()), {"log": []})
    return {"log": values["log"]}


def _drain(seconds: float = 4.0) -> None:
    Worker(["default", "media"], worker_id="test-worker").run(idle_exit_s=seconds)


def _job(job_id) -> Job:
    with session_scope() as db:
        job = db.get(Job, job_id)
        db.expunge(job)
        return job


def _fast_retries(job_id) -> None:
    with session_scope() as db:
        for child in db.execute(select(Job).where(Job.parent_id == job_id)).scalars():
            child.run_after = utcnow()


def test_workflow_children_approval_and_completion():
    COUNTS.clear()
    FAIL_ONCE.clear()
    FAIL_ONCE.add("child-5")  # one child fails transiently, then succeeds on retry
    with session_scope() as db:
        job_id = queue.enqueue(db, "test.workflow", {}).id
    _drain()
    _fast_retries(job_id)
    _drain()
    job = _job(job_id)
    assert job.status == "waiting" and job.status_label == "Waiting for your approval"
    with session_scope() as db:
        children = db.execute(select(Job).where(Job.parent_id == job_id)).scalars().all()
        assert {c.status for c in children} == {"completed"}
        assert max(c.attempts for c in children) == 2  # the retried child
        queue.resume_with(db, job_id, {"approved": True})
    _drain()
    job = _job(job_id)
    assert job.status == "completed", job.error
    assert job.result["doubled"] == {"n2": 4, "n5": 10}
    assert job.result["approved"] is True
    assert job.result["log"] == ["a", "b", "delegate", "approve"]
    assert COUNTS["a"] == 1  # never re-executed across 3 resumptions


def test_resume_from_checkpoint_after_failure():
    COUNTS.clear()
    FAIL_ONCE.clear()
    FAIL_ONCE.add("crash_b")
    with session_scope() as db:
        job_id = queue.enqueue(db, "test.workflow", {}).id
    _drain()
    job = _job(job_id)
    assert job.status == "failed" and "simulated crash" in job.error["message"]
    with session_scope() as db:
        queue.retry(db, job_id)
    _drain()
    assert _job(job_id).status == "waiting"
    assert COUNTS == {"a": 1, "b": 2}  # node a came from the checkpoint; only b re-ran


def test_stale_lease_is_recovered():
    with session_scope() as db:
        job = queue.enqueue(db, "test.child", {"n": 1}, queue="media")
        job.status = "running"
        job.locked_by = "dead-worker"
        job.lease_expires_at = utcnow() - timedelta(seconds=5)
        job_id = job.id
    assert queue.recover_stale() == 1
    assert _job(job_id).status == "queued"
    _drain(2)
    assert _job(job_id).status == "completed"
    with session_scope() as db:
        messages = [e.message for e in db.execute(select(JobEvent).where(JobEvent.job_id == job_id)).scalars()]
    assert any("re-queued" in m for m in messages)


def test_dedupe_and_concurrent_claims():
    with session_scope() as db:
        first = queue.enqueue(db, "test.child", {"n": 1}, dedupe_key="same")
        second = queue.enqueue(db, "test.child", {"n": 1}, dedupe_key="same")
        assert first.id == second.id
        for n in range(8):
            queue.enqueue(db, "test.child", {"n": n}, queue="media")
    claimed: list = []
    lock = threading.Lock()

    def grab(name):
        while True:
            job = queue.claim(name, ["media", "default"], 60)
            if job is None:
                return
            with lock:
                claimed.append(job.id)

    threads = [threading.Thread(target=grab, args=(f"w{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(claimed) == 9 and len(set(claimed)) == 9  # every job claimed exactly once


def test_cancel_waiting_parent_cancels_children():
    COUNTS.clear()
    FAIL_ONCE.clear()
    with session_scope() as db:
        job_id = queue.enqueue(db, "test.workflow", {}).id
    Worker(["default"], worker_id="only-default").run(idle_exit_s=2)  # children stay queued (no media worker)
    assert _job(job_id).status == "waiting"
    with session_scope() as db:
        queue.cancel(db, job_id)
    with session_scope() as db:
        statuses = {j.status for j in db.execute(select(Job).where(Job.parent_id == job_id)).scalars()}
    assert _job(job_id).status == "cancelled" and statuses == {"cancelled"}


def test_pause_and_unpause_queued_job():
    with session_scope() as db:
        job_id = queue.enqueue(db, "test.child", {"n": 3}, queue="media").id
        queue.pause(db, job_id)
    _drain(2)
    assert _job(job_id).status == "paused"
    with session_scope() as db:
        queue.unpause(db, job_id)
    _drain(2)
    assert _job(job_id).status == "completed"


def test_resumes_do_not_consume_retry_budget():
    """Regression: each resume after waiting used to count as an attempt, so a transient
    failure late in a long workflow failed immediately instead of being retried."""
    FAIL_ONCE.clear()
    FAIL_ONCE.add("glitch")
    with session_scope() as db:
        job_id = queue.enqueue(db, "test.long_workflow", {}, max_attempts=3).id
    _drain()  # runs children, suspends for approval
    assert _job(job_id).status == "waiting"
    with session_scope() as db:
        queue.resume_with(db, job_id, {"approved": True})
    _drain()  # third run: the glitch must be retried, not fatal
    job = _job(job_id)
    assert job.status == "queued" and job.status_label == "Retrying", (job.status, job.error)
    with session_scope() as db:
        db.get(Job, job_id).run_after = utcnow()
    _drain()
    job = _job(job_id)
    assert job.status == "completed", job.error
    assert job.result["log"] == ["delegate", "approve", "glitch"]
    assert job.attempts == 2  # one real failure + the successful retry


# ------------------------------------------------- Retry after a step failed for good
BROKEN: set[str] = set()


@handler("test.flaky_step", queue="media")
def _flaky_step(ctx):
    if "step" in BROKEN:
        raise PermanentError("no speech model installed")  # fails for good until the cause is fixed
    return {"value": 42}


def node_strict(state: WFState, config) -> WFState:
    outcome = await_children(config, "step", [ChildSpec(key="s", job_type="test.flaky_step", payload={}, queue="media")],
                             label="Transcribing")["s"]
    if not outcome.ok:  # like the video workflow: a failed step fails the whole run
        raise PermanentError(outcome.error["message"])
    return {"log": [f"step={outcome.result['value']}"]}


@handler("test.strict_workflow")
def _strict_wf(ctx):
    g = StateGraph(WFState)
    g.add_node("strict", node_strict)
    g.add_edge(START, "strict")
    g.add_edge("strict", END)
    values = run_graph_job(ctx, g.compile(checkpointer=get_checkpointer()), {"log": []})
    return {"log": values["log"]}


def test_retry_reruns_the_failed_step_instead_of_replaying_its_failure():
    """Retry after fixing the cause (e.g. installing the speech model) must run the failed step
    again and continue, not replay the stored failure."""
    BROKEN.clear()
    BROKEN.add("step")
    with session_scope() as db:
        job_id = queue.enqueue(db, "test.strict_workflow", {}).id
    _drain()
    job = _job(job_id)
    assert job.status == "failed" and "no speech model" in job.error["message"]
    BROKEN.clear()  # the cause is fixed
    with session_scope() as db:
        queue.retry(db, job_id)
    _drain()
    job = _job(job_id)
    assert job.status == "completed", job.error
    assert job.result["log"] == ["step=42"]
    # And a second failure + retry works the same way.
    BROKEN.add("step")
    with session_scope() as db:
        job_id = queue.enqueue(db, "test.strict_workflow", {}).id
    _drain()
    with session_scope() as db:
        queue.retry(db, job_id)
    _drain()
    assert _job(job_id).status == "failed"  # still broken: fails again, clearly
    BROKEN.clear()
    with session_scope() as db:
        queue.retry(db, job_id)
    _drain()
    assert _job(job_id).status == "completed"
