"""A/B tests of Shorts choices across clips (P3): title option, first-frame text, text hook on/off.

YouTube and Instagram don't let an app show two titles of the same Short to different viewers, so
these tests split *clips*: each new clip goes into arm A or B (balanced), it is published as usual,
and once both arms have enough clips with early-retention numbers (P2) they are compared with a
permutation test. Small samples are reported as such; a result needs p < 0.1 to name a winner.
One test runs at a time; switching the test in Settings stops the old one (its data is kept).
"""

from __future__ import annotations

import hashlib
import statistics
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.base import utcnow
from ..db.models import ABTest, Clip

ARMS = {
    "title": {"A": "best-ranked title", "B": "second title option"},
    "first_frame": {"A": "best first-frame text", "B": "second first-frame text"},
    "hook": {"A": "text on frame one", "B": "no text on frame one (spoken opening only)"},
}


def current(db: Session, dimension: str, min_per_arm: int) -> ABTest:
    """The running test for ``dimension``; other running tests are stopped (one at a time)."""
    running = db.execute(select(ABTest).where(ABTest.status == "running")).scalars().all()
    test = None
    for t in running:
        if t.dimension == dimension and test is None:
            test = t
        else:
            t.status, t.ended_at = "stopped", utcnow()
    if test is None:
        test = ABTest(dimension=dimension, min_per_arm=min_per_arm, arms=ARMS[dimension], result={})
        db.add(test)
        db.flush()
    return test


def _clips_in(db: Session, test_id) -> list[Clip]:
    return list(db.execute(select(Clip).where(Clip.shorts["ab"]["test_id"].astext == str(test_id))).scalars())


def assign(db: Session, clip_id: uuid.UUID, dimension: str, min_per_arm: int) -> dict:
    """This clip's arm (kept on re-render), balancing the two arms."""
    clip = db.get(Clip, clip_id)
    existing = (clip.shorts or {}).get("ab") if clip else None
    test = current(db, dimension, min_per_arm)
    if existing and existing.get("test_id") == str(test.id):
        return existing
    counts = {"A": 0, "B": 0}
    for other in _clips_in(db, test.id):
        counts[(other.shorts or {})["ab"]["arm"]] += 1
    if counts["A"] != counts["B"]:
        arm = "A" if counts["A"] < counts["B"] else "B"
    else:
        arm = "A" if int(hashlib.sha256(str(clip_id).encode()).hexdigest(), 16) % 2 == 0 else "B"
    return {"test_id": str(test.id), "dimension": dimension, "arm": arm, "means": ARMS[dimension][arm]}


def evaluate(db: Session) -> list[dict]:
    """Compare the arms of every running test on early retention; conclude when both are big enough."""
    from ..analytics import retention
    from ..analytics.compare import permutation_test

    out = []
    for test in db.execute(select(ABTest).where(ABTest.status.in_(("running", "concluded", "stopped")))
                           .order_by(ABTest.started_at.desc()).limit(10)).scalars():
        values: dict[str, dict[str, list[float]]] = {"A": {}, "B": {}}
        assigned = {"A": 0, "B": 0}
        for clip in _clips_in(db, test.id):
            arm = clip.shorts["ab"]["arm"]
            assigned[arm] += 1
            for info in ((clip.shorts or {}).get("analytics") or {}).values():
                main = retention.primary(info.get("signals") or {})
                if main:
                    values[arm].setdefault(main[0], []).append(main[1])
        metrics = set(values["A"]) & set(values["B"])
        metric = max(metrics, key=lambda m: min(len(values["A"][m]), len(values["B"][m])), default=None)
        a = values["A"].get(metric, []) if metric else []
        b = values["B"].get(metric, []) if metric else []
        summary = {"metric": metric, "assigned": assigned, "measured": {"A": len(a), "B": len(b)},
                   "mean": {"A": round(statistics.mean(a), 4) if a else None, "B": round(statistics.mean(b), 4) if b else None}}
        if test.status == "running" and len(a) >= test.min_per_arm and len(b) >= test.min_per_arm:
            diff, p = permutation_test(b, a)
            winner = ("B" if diff > 0 else "A") if p < 0.1 else None
            summary.update(difference=round(diff, 4), p_value=round(p, 4), winner=winner,
                           conclusion=(f"{ARMS[test.dimension][winner]} kept viewers better ({metric}, p={p:.2f})"
                                       if winner else f"no clear difference yet ({metric}, p={p:.2f}); small samples"))
            test.status, test.ended_at = "concluded", utcnow()
        test.result = {**(test.result or {}), **summary}
        out.append({"id": str(test.id), "dimension": test.dimension, "status": test.status, "arms": test.arms,
                    "min_per_arm": test.min_per_arm, "started_at": test.started_at, "ended_at": test.ended_at,
                    **test.result})
    return out
