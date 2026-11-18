"""Continuous learning engine (Part 18).

OBSERVE → COLLECT → ANALYZE → COMPARE → IDENTIFY PATTERNS → SAVE LESSONS →
TEST STRATEGY → APPLY → MEASURE AGAIN.

* Observational comparisons produce ``correlation`` records — never causal
  claims — with sample sizes, effect sizes, p-values and limitations.
* Promising correlations do not silently change behaviour: they produce a new
  *candidate* strategy version and a randomized experiment (a share of new
  projects use the variant). Only a concluded experiment can promote a
  variant, and only that record carries ``causal_claim=True``.
* Strategies are versioned and never overwritten; history stays inspectable.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..analytics.compare import Row, dataset, permutation_test
from ..db.base import utcnow
from ..db.models import Experiment, LearningRecord, Strategy
from ..media.highlights import DEFAULT_WEIGHTS

log = logging.getLogger(__name__)

MIN_GROUP = 5
P_THRESHOLD = 0.10
MIN_EFFECT = 0.20  # log-ratio ≈ 22% difference in views relative to the platform median
DEFAULT_PARAMS = {"weights": dict(DEFAULT_WEIGHTS), "target_seconds": None, "interests": []}


@dataclass
class Dimension:
    key: str
    label_a: str
    label_b: str
    split: Callable[[Row, dict], bool | None]  # True -> group A, False -> group B, None -> excluded
    lever: str  # strategy parameter this dimension can inform


def _hook_has_number(r: Row, _ctx) -> bool:
    return bool(r.features.get("number_in_hook"))


DIMENSIONS = [
    Dimension("duration", "clips of 30 seconds or less", "clips longer than 30 seconds",
              lambda r, _c: r.duration <= 30.0, "target_seconds"),
    Dimension("question_hook", "clips that open with a question", "clips that open with a statement",
              lambda r, _c: bool(r.features.get("question_hook")), "weights.hook"),
    Dimension("number_hook", "clips whose hook contains a number", "clips whose hook has no number",
              _hook_has_number, "weights.hook"),
    Dimension("hook_score", "clips with an above-median hook score", "clips with a below-median hook score",
              lambda r, c: None if "hook" not in r.scores else r.scores["hook"] >= c["hook_median"], "weights.hook"),
    Dimension("face_presence", "clips with the speaker's face visible most of the time", "clips with little face time",
              lambda r, _c: None if r.features.get("face_ratio") is None else r.features["face_ratio"] >= 0.5, "weights.visual"),
    Dimension("information", "information-dense clips", "less information-dense clips",
              lambda r, c: None if "information" not in r.scores else r.scores["information"] >= c["info_median"],
              "weights.information"),
]


def ensure_default_strategy(db: Session) -> Strategy:
    active = db.execute(select(Strategy).where(Strategy.status == "active").order_by(Strategy.version.desc())).scalars().first()
    if active is not None:
        return active
    latest = db.execute(select(func.max(Strategy.version))).scalar_one() or 0
    strategy = Strategy(version=latest + 1, name="Default local strategy", status="active", params=DEFAULT_PARAMS,
                        rationale="Initial weights for local highlight scoring (no performance data yet).",
                        activated_at=utcnow())
    db.add(strategy)
    db.flush()
    return strategy


def _confidence(p: float, n_a: int, n_b: int) -> float:
    size = min(1.0, min(n_a, n_b) / 20)
    return round(max(0.05, min(0.9, (1 - p) * (0.4 + 0.6 * size))), 3)


def analyze(db: Session) -> list[LearningRecord]:
    """Compare groups per platform and record well-supported correlations."""
    strategy = ensure_default_strategy(db)
    records: list[LearningRecord] = []
    rows_all = dataset(db)
    for platform in sorted({r.platform for r in rows_all}):
        rows = [r for r in rows_all if r.platform == platform and r.relative is not None]
        if len(rows) < MIN_GROUP * 2:
            continue
        ctx = {"hook_median": float(np.median([r.scores.get("hook", 0) for r in rows])),
               "info_median": float(np.median([r.scores.get("information", 0) for r in rows]))}
        for dim in DIMENSIONS:
            a = [r for r in rows if dim.split(r, ctx) is True]
            b = [r for r in rows if dim.split(r, ctx) is False]
            if len(a) < MIN_GROUP or len(b) < MIN_GROUP:
                continue
            effect, p = permutation_test([r.relative for r in a], [r.relative for r in b])
            if p > P_THRESHOLD or abs(effect) < MIN_EFFECT:
                continue
            better, worse = (dim.label_a, dim.label_b) if effect > 0 else (dim.label_b, dim.label_a)
            ratio = float(np.exp(abs(effect)))
            finding = (f"On {platform.title()}, {better} reached {ratio:.1f}× the views (relative to the platform median) "
                       f"of {worse} ({len(a)} vs {len(b)} videos, p={p:.3f}).")
            duplicate = db.execute(select(LearningRecord).where(LearningRecord.status == "active",
                                                                LearningRecord.platform == platform,
                                                                LearningRecord.evidence["dimension"].astext == dim.key)).scalars().first()
            if duplicate is not None:
                duplicate.status = "superseded"
            evidence = {
                "dimension": dim.key, "lever": dim.lever, "group_a": dim.label_a, "group_b": dim.label_b, "n_a": len(a),
                "n_b": len(b), "mean_relative_a": round(float(np.mean([r.relative for r in a])), 3),
                "mean_relative_b": round(float(np.mean([r.relative for r in b])), 3), "effect_log_ratio": round(effect, 3),
                "p_value": round(p, 4), "test": "two-sided permutation test on mean log(views / platform median)",
                "favours": "a" if effect > 0 else "b",
            }
            engagement_a = [r.engagement_rate for r in a if r.engagement_rate is not None]
            engagement_b = [r.engagement_rate for r in b if r.engagement_rate is not None]
            if engagement_a and engagement_b:
                evidence["engagement_rate_a"] = round(float(np.mean(engagement_a)), 4)
                evidence["engagement_rate_b"] = round(float(np.mean(engagement_b)), 4)
            record = LearningRecord(
                kind="correlation", finding=finding, evidence=evidence,
                related_publication_ids=[r.publication_id for r in a + b], platform=platform,
                confidence=_confidence(p, len(a), len(b)),
                limitations=("Observational correlation, not proven causation: topic, posting time and audience "
                             "trends may confound it. Views alone don't measure quality."),
                causal_claim=False, strategy_id=strategy.id,
                next_experiment=f"Randomized test: favour {better} for a share of new clips and compare against the current strategy.",
            )
            db.add(record)
            records.append(record)
    db.flush()
    return records


def _apply_lever(params: dict[str, Any], record: LearningRecord) -> dict[str, Any] | None:
    lever = record.evidence["lever"]
    favours = record.evidence["favours"]
    new = {"weights": dict(params.get("weights") or DEFAULT_WEIGHTS), "target_seconds": params.get("target_seconds"),
           "interests": list(params.get("interests") or [])}
    if lever == "target_seconds":
        new["target_seconds"] = 24.0 if favours == "a" else 42.0
    elif lever.startswith("weights."):
        key = lever.split(".", 1)[1]
        factor = 1.2 if favours == "a" else 0.85  # bounded change per learning step
        new["weights"][key] = round(min(0.4, max(0.02, new["weights"].get(key, 0.1) * factor)), 4)
    else:
        return None
    return new if new != params else None


def propose_experiments(db: Session, records: list[LearningRecord]) -> list[Experiment]:
    """Turn strong correlations into a candidate strategy + randomized experiment."""
    if db.execute(select(Experiment).where(Experiment.status == "running")).scalars().first():
        return []  # one experiment at a time keeps attribution clean
    strong = sorted([r for r in records if r.confidence >= 0.5], key=lambda r: r.confidence, reverse=True)
    if not strong:
        return []
    record = strong[0]
    active = ensure_default_strategy(db)
    params = _apply_lever(active.params, record)
    if params is None:
        return []
    version = (db.execute(select(func.max(Strategy.version))).scalar_one() or 0) + 1
    candidate = Strategy(version=version, parent_id=active.id, name=f"v{version}: {record.evidence['dimension']} adjustment",
                         status="experiment", params=params, rationale=record.finding, evidence_record_ids=[str(record.id)])
    db.add(candidate)
    db.flush()
    experiment = Experiment(name=f"Test {record.evidence['dimension']} ({record.platform})",
                            hypothesis=record.next_experiment, platform=record.platform, control_strategy_id=active.id,
                            variant_strategy_id=candidate.id, metric="relative_views", allocation=0.3,
                            min_samples_per_arm=8)
    db.add(experiment)
    db.flush()
    return [experiment]


def evaluate_experiments(db: Session) -> list[LearningRecord]:
    results: list[LearningRecord] = []
    rows = dataset(db)
    for experiment in db.execute(select(Experiment).where(Experiment.status == "running")).scalars().all():
        control = [r.relative for r in rows if r.strategy_id == str(experiment.control_strategy_id) and r.relative is not None
                   and (experiment.platform is None or r.platform == experiment.platform)]
        variant = [r.relative for r in rows if r.strategy_id == str(experiment.variant_strategy_id) and r.relative is not None
                   and (experiment.platform is None or r.platform == experiment.platform)]
        if len(control) < experiment.min_samples_per_arm or len(variant) < experiment.min_samples_per_arm:
            continue
        effect, p = permutation_test(variant, control)
        win = effect > 0 and p <= P_THRESHOLD
        experiment.status = "concluded"
        experiment.ended_at = utcnow()
        experiment.result = {"n_control": len(control), "n_variant": len(variant), "effect_log_ratio": round(effect, 3),
                             "p_value": round(p, 4), "winner": "variant" if win else "control"}
        variant_strategy = db.get(Strategy, experiment.variant_strategy_id)
        if win:
            for old in db.execute(select(Strategy).where(Strategy.status == "active")).scalars():
                old.status = "retired"
            variant_strategy.status = "active"
            variant_strategy.activated_at = utcnow()
        else:
            variant_strategy.status = "retired"
        record = LearningRecord(
            kind="experiment_result",
            finding=(f"Experiment “{experiment.name}”: the variant strategy {'outperformed' if win else 'did not outperform'} "
                     f"the control (effect {effect:+.2f} log-ratio, p={p:.3f}, {len(variant)} vs {len(control)} videos)."),
            evidence=experiment.result, related_publication_ids=[], platform=experiment.platform,
            confidence=_confidence(p, len(variant), len(control)),
            limitations="Randomized assignment supports a causal reading for this channel and period; effects can change "
                        "as audience and platform algorithms change.",
            causal_claim=win, strategy_id=variant_strategy.id if win else experiment.control_strategy_id,
            next_experiment="Re-check after the next 20 publications; consider testing the next strongest finding.",
        )
        db.add(record)
        results.append(record)
    db.flush()
    return results


def assign_strategy(db: Session, project_id: uuid.UUID) -> tuple[Strategy, str | None]:
    """Deterministic randomized assignment for running experiments."""
    active = ensure_default_strategy(db)
    experiment = db.execute(select(Experiment).where(Experiment.status == "running")).scalars().first()
    if experiment is None:
        return active, None
    bucket = int(hashlib.sha256(f"{experiment.id}:{project_id}".encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    if bucket < experiment.allocation:
        return db.get(Strategy, experiment.variant_strategy_id), "B"
    return db.get(Strategy, experiment.control_strategy_id), "A"


def run(db: Session) -> dict[str, Any]:
    findings = analyze(db)
    concluded = evaluate_experiments(db)
    started = propose_experiments(db, findings)
    try:
        from ..memory.service import get_memory

        for record in findings + concluded:
            get_memory().remember(record.finding, agent=True, category="lesson", infer=False,
                                  metadata={"learning_record_id": str(record.id), "confidence": record.confidence,
                                            "causal": record.causal_claim})
    except Exception as exc:  # memory index is an accelerator only
        log.warning("Could not index lessons in memory: %s", exc)
    return {"findings": len(findings), "experiments_concluded": len(concluded), "experiments_started": len(started)}
