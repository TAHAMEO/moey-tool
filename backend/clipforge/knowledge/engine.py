"""Cross-model knowledge engine (Part 9).

Flow: detect gap -> reuse stored knowledge if a close match exists -> ask one
(or, for important questions or low confidence, several distinct) connected
providers -> compare answers and individual claims with local embeddings ->
record agreement, disagreements, confidence and an honest verification status
-> index for reuse.

Nothing here retrains any provider. The Brain improves by remembering vetted
findings and retrieving them later. External answers are never treated as
verified merely because a model said so: a single answer is
``single_source``; agreeing independent answers are ``consensus``;
conflicting answers are ``disputed``; only the owner (or matching internal
evidence) can mark an item ``verified``.
"""

from __future__ import annotations

import itertools
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.base import session_scope
from ..db.models import KnowledgeItem
from ..providers.base import ChatMessage, NoProviderAvailable
from ..providers.embeddings import cosine, get_embedder
from ..providers.hub import TaskSpec, hub
from ..security.untrusted import GUARD_INSTRUCTION
from ..services import app_settings

log = logging.getLogger(__name__)

REUSE_SIMILARITY = 0.86
CLAIM_MATCH = 0.78
SYSTEM_PROMPT = (
    "You are a careful expert assisting a video production system for YouTube Shorts and Instagram Reels. "
    "Answer concisely and factually. List the distinct factual claims your answer depends on. If you are not "
    "sure or the facts may have changed recently, say so in caveats and lower your confidence. "
    + GUARD_INSTRUCTION
)


class ProviderAnswer(BaseModel):
    answer: str = Field(max_length=4000)
    claims: list[str] = Field(default_factory=list, max_length=12)
    confidence: float = Field(0.5, ge=0, le=1)
    caveats: str = ""


@dataclass
class KnowledgeResult:
    item_id: uuid.UUID | None
    answer: str
    confidence: float
    verification_status: str
    providers: list[str]
    reused: bool = False
    disagreements: list[str] = field(default_factory=list)
    cost_usd: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "knowledge_id": str(self.item_id) if self.item_id else None,
            "answer": self.answer,
            "confidence": round(self.confidence, 2),
            "verification_status": self.verification_status,
            "providers": self.providers,
            "reused": self.reused,
            "disagreements": self.disagreements,
            "cost_usd": round(self.cost_usd, 6),
        }


def _topic(question: str) -> str:
    words = [w for w in question.lower().replace("?", "").split() if len(w) > 3][:6]
    return " ".join(words)[:200] or "general"


def find_reusable(db: Session, question: str) -> tuple[KnowledgeItem | None, float]:
    query_vec = get_embedder().embed_one(question)
    best: tuple[KnowledgeItem | None, float] = (None, 0.0)
    rows = db.execute(
        select(KnowledgeItem).where(KnowledgeItem.verification_status != "rejected")
        .order_by(KnowledgeItem.updated_at.desc()).limit(500)
    ).scalars()
    for item in rows:
        score = cosine(query_vec, get_embedder().embed_one(item.question))
        if score > best[1]:
            best = (item, score)
    return best


def _compare(answers: list[tuple[str, ProviderAnswer]]) -> tuple[float, list[str]]:
    embedder = get_embedder()
    vectors = [embedder.embed_one(a.answer) for _, a in answers]
    pairs = list(itertools.combinations(range(len(answers)), 2))
    agreement = sum(cosine(vectors[i], vectors[j]) for i, j in pairs) / len(pairs) if pairs else 1.0
    disagreements: list[str] = []
    for (name_a, a), (name_b, b) in itertools.permutations(answers, 2):
        if not b.claims:
            continue
        b_vectors = [embedder.embed_one(c) for c in b.claims]
        for claim in a.claims:
            best = max(cosine(embedder.embed_one(claim), v) for v in b_vectors)
            if best < CLAIM_MATCH:
                disagreements.append(f"{name_a} claims “{claim[:160]}” — not supported by {name_b}")
    return agreement, sorted(set(disagreements))[:10]


def ask(question: str, *, project_id: uuid.UUID | None = None, important: bool = False,
        job_id: uuid.UUID | None = None) -> KnowledgeResult:
    """Answer a knowledge question using stored knowledge first, then providers."""
    with session_scope() as db:
        item, score = find_reusable(db, question)
        if item is not None and score >= REUSE_SIMILARITY and item.confidence >= 0.45:
            item.reuse_count += 1
            return KnowledgeResult(item.id, item.answer, item.confidence, item.verification_status,
                                   item.providers, reused=True, disagreements=item.disagreements)
        max_providers = app_settings.get_section(db, "ai").consensus_max_providers

    messages = [ChatMessage("system", SYSTEM_PROMPT), ChatMessage("user", question)]
    answers: list[tuple[str, ProviderAnswer]] = []
    responses: list[dict[str, Any]] = []
    used: set[uuid.UUID] = set()
    cost = 0.0
    target = max_providers if important else 1
    for _ in range(max(1, target)):
        try:
            parsed, result = hub.complete_json(
                TaskSpec("knowledge", quality=2, temperature=0.2, max_tokens=700, cache_ttl_s=30 * 24 * 3600,
                         exclude_connections=frozenset(used), max_providers=2, job_id=job_id, project_id=project_id),
                messages, ProviderAnswer,
            )
        except NoProviderAvailable:
            break
        if result.connection_id is not None:
            used.add(result.connection_id)
        name = f"{result.connection_name}/{result.model}"
        answers.append((name, parsed))
        cost += result.cost_usd
        responses.append({"provider": result.connection_name, "model": result.model, "answer": parsed.answer,
                          "claims": parsed.claims, "confidence": parsed.confidence, "caveats": parsed.caveats,
                          "cached": result.cached})
        # Low self-reported confidence escalates to a second opinion.
        if len(answers) == 1 and parsed.confidence < 0.6 and max_providers > 1:
            target = max(target, 2)
        if len(answers) >= target:
            break
    if not answers:
        raise NoProviderAvailable("No AI provider is available to answer knowledge questions")

    agreement, disagreements = _compare(answers) if len(answers) > 1 else (None, [])
    self_conf = sum(a.confidence for _, a in answers) / len(answers)
    if len(answers) == 1:
        status, confidence = "single_source", min(self_conf, 0.6)
    elif agreement is not None and agreement >= 0.80 and len(disagreements) <= 1:
        status, confidence = "consensus", min(0.9, 0.5 + 0.4 * agreement)
    else:
        status, confidence = "disputed", min(self_conf, 0.4)
    best_answer = max(answers, key=lambda pair: pair[1].confidence)[1]
    with session_scope() as db:
        item = KnowledgeItem(
            topic=_topic(question), question=question, answer=best_answer.answer, responses=responses,
            providers=[name for name, _ in answers], agreement=agreement, disagreements=disagreements,
            evidence=[{"type": "model_responses", "count": len(answers)}], confidence=confidence,
            verification_status=status, project_id=project_id,
        )
        db.add(item)
        db.flush()
        item_id = item.id
    try:
        from ..memory.service import get_memory

        get_memory().remember(f"Q: {question}\nA: {best_answer.answer[:600]}", agent=True, category="knowledge",
                              metadata={"knowledge_id": str(item_id), "verification": status}, infer=False)
    except Exception as exc:  # memory index is an accelerator; the row is authoritative
        log.warning("Could not index knowledge item in memory: %s", exc)
    return KnowledgeResult(item_id, best_answer.answer, confidence, status, [n for n, _ in answers],
                           disagreements=disagreements, cost_usd=cost)


def set_verification(db: Session, item_id: uuid.UUID, status: str, *, note: str = "") -> KnowledgeItem:
    if status not in {"verified", "rejected", "single_source", "consensus", "disputed"}:
        raise ValueError("Invalid verification status")
    item = db.get(KnowledgeItem, item_id)
    if item is None:
        raise LookupError("Knowledge item not found")
    item.verification_status = status
    if status == "verified":
        item.confidence = max(item.confidence, 0.9)
    elif status == "rejected":
        item.confidence = 0.0
    item.evidence = [*item.evidence, {"type": "owner_review", "status": status, "note": note[:500]}]
    return item
