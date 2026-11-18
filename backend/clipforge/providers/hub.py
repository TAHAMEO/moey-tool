"""AI Model Connection Hub: routing, fallback, budgets, caching, cost tracking.

The Brain never names a model. It describes a *task* (quality needed, JSON,
vision, context size) and the hub picks the most suitable connected model,
preferring free local models when the owner's local-first policy is on and
spending money only within the configured budgets. Failures are classified;
retryable ones put the connection on a short cooldown and the next candidate
is tried immediately. When nothing can serve the task the caller receives
``NoProviderAvailable`` and uses its local (non-LLM) implementation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar
from urllib.parse import urlsplit

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..db.base import session_scope, utcnow
from ..db.models import AIUsage, LLMCache, ProviderConnection, ProviderModel
from ..security.crypto import SecretDecryptionError, SecretsUnavailable, read_secret
from ..services import app_settings
from . import catalog
from .adapters import build_adapter
from .base import ChatMessage, ChatParams, NoProviderAvailable, ProviderError

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


@dataclass
class TaskSpec:
    name: str
    quality: int = 2  # 1 fast/cheap, 2 balanced, 3 best
    needs_json: bool = False
    needs_vision: bool = False
    min_context: int = 0
    allow_paid: bool = True
    cache_ttl_s: int | None = None
    temperature: float = 0.2
    max_tokens: int = 800
    timeout_s: float = 90.0
    job_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    max_providers: int = 3
    exclude_connections: frozenset[uuid.UUID] = frozenset()
    prefer_connection: uuid.UUID | None = None


@dataclass
class Candidate:
    connection_id: uuid.UUID
    connection_name: str
    provider_type: str
    model_id: str
    is_local: bool
    quality_tier: int
    price_in: float
    price_out: float
    price_estimated: bool
    context_window: int
    est_cost: float
    score: float


@dataclass
class HubResult:
    text: str
    provider_type: str
    connection_id: uuid.UUID | None
    connection_name: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cost_estimated: bool = False
    latency_ms: int = 0
    cached: bool = False
    attempts: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "provider": self.connection_name,
            "provider_type": self.provider_type,
            "model": self.model,
            "cost_usd": round(self.cost_usd, 6),
            "cached": self.cached,
            "fallbacks": max(0, len([a for a in self.attempts if not a.get("ok")])),
        }


def is_local_url(base_url: str | None) -> bool:
    if not base_url:
        return False
    host = (urlsplit(base_url).hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1", "host.docker.internal", "ollama", "llamacpp"}:
        return True
    try:
        import ipaddress

        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return host.endswith((".local", ".internal", ".lan"))


def _cache_key(task: TaskSpec, messages: list[ChatMessage], schema: dict | None) -> str:
    payload = {
        "task": task.name,
        "q": task.quality,
        "t": round(task.temperature, 2),
        "mt": task.max_tokens,
        "schema": schema,
        "messages": [
            {
                "r": m.role,
                "c": m.content,
                "i": [hashlib.sha256(img.data).hexdigest() for img in m.images],
            }
            for m in messages
        ],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def extract_json(text: str) -> Any:
    """Parse the first JSON object/array in model output (tolerates code fences)."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except ValueError:
                continue
    raise ValueError("No JSON found in model output")


class ModelHub:
    """Stateless façade; every call opens its own short DB transactions."""

    # ------------------------------------------------------------------ spend
    @staticmethod
    def spend(db: Session, *, since: datetime | None = None, job_id: uuid.UUID | None = None) -> float:
        query = select(func.coalesce(func.sum(AIUsage.cost_usd), 0.0))
        if since is not None:
            query = query.where(AIUsage.created_at >= since)
        if job_id is not None:
            query = query.where(AIUsage.job_id == job_id)
        return float(db.execute(query).scalar_one())

    @staticmethod
    def _period_starts() -> tuple[datetime, datetime]:
        now = datetime.now(UTC)
        day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return day, day.replace(day=1)

    # ------------------------------------------------------------- candidates
    def candidates(self, db: Session, task: TaskSpec, prompt_chars: int) -> tuple[list[Candidate], list[str]]:
        ai = app_settings.get_section(db, "ai")
        control = app_settings.get_section(db, "control")
        skipped: list[str] = []
        now = utcnow()
        day_start, month_start = self._period_starts()
        spent_day = self.spend(db, since=day_start)
        spent_month = self.spend(db, since=month_start)
        spent_job = self.spend(db, job_id=task.job_id) if task.job_id else 0.0
        in_tokens = catalog.estimate_tokens("x" * prompt_chars)
        out_tokens = task.max_tokens

        rows = db.execute(
            select(ProviderConnection)
            .options(selectinload(ProviderConnection.models))
            .where(ProviderConnection.enabled.is_(True))
        ).scalars().all()

        result: list[Candidate] = []
        for conn in rows:
            if conn.id in task.exclude_connections:
                continue
            if f"provider:{conn.provider_type}" in control.disabled_integrations:
                skipped.append(f"{conn.name}: disabled in settings")
                continue
            if conn.status in {"invalid_credentials", "quota_exhausted"} and conn.cooldown_until and conn.cooldown_until > now:
                skipped.append(f"{conn.name}: {conn.status}")
                continue
            if conn.cooldown_until and conn.cooldown_until > now:
                skipped.append(f"{conn.name}: cooling down after errors")
                continue
            if control.emergency_stop and not conn.is_local:
                skipped.append(f"{conn.name}: emergency stop blocks external providers")
                continue
            for model in conn.models:
                if not model.enabled or not model.capabilities.get("chat", True):
                    continue
                if task.needs_vision and not model.capabilities.get("vision"):
                    continue
                context = model.context_window or catalog.default_context(conn.provider_type, model.model_id)
                if task.min_context and context < task.min_context:
                    continue
                if conn.is_local:
                    price_in = price_out = 0.0
                    estimated = False
                else:
                    estimated = model.price_in_per_mtok is None or model.price_source.startswith("reference")
                    price_in = model.price_in_per_mtok if model.price_in_per_mtok is not None else catalog.UNKNOWN_PRICE_IN
                    price_out = model.price_out_per_mtok if model.price_out_per_mtok is not None else catalog.UNKNOWN_PRICE_OUT
                est_cost = (in_tokens * price_in + out_tokens * price_out) / 1_000_000
                paid = est_cost > 0
                if paid:
                    if not (ai.allow_paid_apis and task.allow_paid):
                        skipped.append(f"{conn.name}/{model.model_id}: paid APIs not allowed for this task")
                        continue
                    if spent_day + est_cost > ai.daily_budget_usd or spent_month + est_cost > ai.monthly_budget_usd:
                        skipped.append(f"{conn.name}/{model.model_id}: budget limit reached")
                        continue
                    if task.job_id and spent_job + est_cost > ai.per_job_budget_usd:
                        skipped.append(f"{conn.name}/{model.model_id}: per-job budget reached")
                        continue
                tier = model.quality_tier
                score = 0.0
                if tier >= task.quality:
                    score += 2.0 - 0.6 * (tier - task.quality)  # don't overshoot needlessly
                else:
                    score -= 2.5 * (task.quality - tier)
                score -= est_cost * 300.0
                if conn.is_local and ai.local_first:
                    score += 1.5
                score += (model.success_ema - 0.8) * 3.0
                if model.latency_ms_ema:
                    score -= min(model.latency_ms_ema, 60000) / 20000.0
                if task.prefer_connection and conn.id == task.prefer_connection:
                    score += 5.0
                result.append(
                    Candidate(conn.id, conn.name, conn.provider_type, model.model_id, conn.is_local, tier,
                              price_in, price_out, estimated, context, est_cost, score)
                )
        result.sort(key=lambda c: c.score, reverse=True)
        # One model per connection in the attempt list: falling back to a
        # sibling model on the same failing endpoint rarely helps.
        seen: set[uuid.UUID] = set()
        unique: list[Candidate] = []
        for cand in result:
            if cand.connection_id in seen:
                continue
            seen.add(cand.connection_id)
            unique.append(cand)
        return unique, skipped

    # ------------------------------------------------------------- execution
    def complete(self, task: TaskSpec, messages: list[ChatMessage], *, json_schema: dict | None = None) -> HubResult:
        prompt_chars = sum(len(m.content) for m in messages)
        cache_key = None
        if task.cache_ttl_s and task.temperature <= 0.3:
            cache_key = _cache_key(task, messages, json_schema)
            with session_scope() as db:
                hit = db.get(LLMCache, cache_key)
                if hit is not None and (hit.expires_at is None or hit.expires_at > utcnow()):
                    hit.hits += 1
                    data = hit.response
                    db.add(AIUsage(provider_type=data.get("provider_type", "cache"), model=data.get("model", ""),
                                   task=task.name, cached=True, success=True, job_id=task.job_id,
                                   project_id=task.project_id))
                    return HubResult(text=data["text"], provider_type=data.get("provider_type", ""),
                                     connection_id=None, connection_name=data.get("connection_name", "cache"),
                                     model=data.get("model", ""), cached=True)

        with session_scope() as db:
            candidates, skipped = self.candidates(db, task, prompt_chars)
        if not candidates:
            reason = "; ".join(skipped[:6]) if skipped else "no AI provider is connected"
            raise NoProviderAvailable(f"No suitable AI model available ({reason})")

        attempts: list[dict[str, Any]] = []
        params = ChatParams(temperature=task.temperature, max_tokens=task.max_tokens,
                            json_mode=task.needs_json, json_schema=json_schema, timeout_s=task.timeout_s)
        for cand in candidates[: task.max_providers]:
            try:
                with session_scope() as db:
                    conn = db.get(ProviderConnection, cand.connection_id)
                    api_key = read_secret(db, conn.secret_id) if conn.secret_id else None
                    adapter = build_adapter(conn.provider_type, api_key=api_key, base_url=conn.base_url, config=conn.config)
            except (SecretsUnavailable, SecretDecryptionError) as exc:
                attempts.append({"provider": cand.connection_name, "model": cand.model_id, "ok": False, "error": "secrets_unavailable"})
                log.warning("Cannot use %s: %s", cand.connection_name, exc)
                continue
            try:
                result = adapter.chat(messages, cand.model_id, params)
            except ProviderError as exc:
                attempts.append({"provider": cand.connection_name, "model": cand.model_id, "ok": False, "error": exc.kind})
                self._record_failure(cand, task, exc)
                continue
            cost = (result.input_tokens * cand.price_in + result.output_tokens * cand.price_out) / 1_000_000
            attempts.append({"provider": cand.connection_name, "model": cand.model_id, "ok": True})
            hub_result = HubResult(
                text=result.text, provider_type=cand.provider_type, connection_id=cand.connection_id,
                connection_name=cand.connection_name, model=cand.model_id, input_tokens=result.input_tokens,
                output_tokens=result.output_tokens, cost_usd=cost, cost_estimated=cand.price_estimated,
                latency_ms=result.latency_ms, attempts=attempts,
            )
            self._record_success(cand, task, hub_result)
            if cache_key:
                self._store_cache(cache_key, task, hub_result)
            return hub_result
        raise NoProviderAvailable(
            "All suitable AI providers failed: " + ", ".join(f"{a['provider']} ({a['error']})" for a in attempts),
            attempts,
        )

    def complete_json(self, task: TaskSpec, messages: list[ChatMessage], schema: type[T]) -> tuple[T, HubResult]:
        """Structured output validated against ``schema``; invalid output falls back."""
        task.needs_json = True
        json_schema = schema.model_json_schema()
        excluded = set(task.exclude_connections)
        last_error: Exception | None = None
        for _ in range(max(1, task.max_providers)):
            attempt_task = TaskSpec(**{**task.__dict__, "exclude_connections": frozenset(excluded), "max_providers": task.max_providers})
            result = self.complete(attempt_task, messages, json_schema=json_schema)
            try:
                return schema.model_validate(extract_json(result.text)), result
            except (ValueError, ValidationError) as exc:
                last_error = exc
                if result.connection_id is None:  # bad cached entry
                    break
                with session_scope() as db:
                    db.add(AIUsage(connection_id=result.connection_id, provider_type=result.provider_type,
                                   model=result.model, task=task.name, success=False, error_kind="invalid_output",
                                   job_id=task.job_id, project_id=task.project_id))
                excluded.add(result.connection_id)
        raise NoProviderAvailable(f"No provider returned valid structured output ({last_error})")

    # --------------------------------------------------------------- records
    def _record_success(self, cand: Candidate, task: TaskSpec, res: HubResult) -> None:
        with session_scope() as db:
            db.add(AIUsage(connection_id=cand.connection_id, provider_type=cand.provider_type, model=cand.model_id,
                           task=task.name, input_tokens=res.input_tokens, output_tokens=res.output_tokens,
                           cost_usd=res.cost_usd, cost_estimated=res.cost_estimated, latency_ms=res.latency_ms,
                           success=True, job_id=task.job_id, project_id=task.project_id))
            model = db.execute(select(ProviderModel).where(ProviderModel.connection_id == cand.connection_id,
                                                           ProviderModel.model_id == cand.model_id)).scalar_one_or_none()
            if model is not None:
                model.calls += 1
                model.success_ema = 0.9 * model.success_ema + 0.1
                model.latency_ms_ema = res.latency_ms if model.latency_ms_ema is None else 0.8 * model.latency_ms_ema + 0.2 * res.latency_ms
            conn = db.get(ProviderConnection, cand.connection_id)
            if conn is not None:
                conn.status = "ok"
                conn.last_error = None
                conn.cooldown_until = None
                conn.config = {**(conn.config or {}), "consecutive_failures": 0}

    def _record_failure(self, cand: Candidate, task: TaskSpec, exc: ProviderError) -> None:
        log.warning("Provider %s/%s failed (%s): %s", cand.connection_name, cand.model_id, exc.kind, exc)
        with session_scope() as db:
            db.add(AIUsage(connection_id=cand.connection_id, provider_type=cand.provider_type, model=cand.model_id,
                           task=task.name, success=False, error_kind=exc.kind, job_id=task.job_id,
                           project_id=task.project_id))
            model = db.execute(select(ProviderModel).where(ProviderModel.connection_id == cand.connection_id,
                                                           ProviderModel.model_id == cand.model_id)).scalar_one_or_none()
            if model is not None:
                model.success_ema = 0.9 * model.success_ema
                if exc.kind in {"not_found", "unsupported"} and exc.status == 404:
                    model.enabled = False  # model id no longer exists
            conn = db.get(ProviderConnection, cand.connection_id)
            if conn is None:
                return
            failures = int((conn.config or {}).get("consecutive_failures", 0)) + 1
            conn.config = {**(conn.config or {}), "consecutive_failures": failures}
            conn.last_error = f"{exc.kind}: {str(exc)[:300]}"
            now = utcnow()
            if exc.kind == "auth":
                conn.status = "invalid_credentials"
                conn.cooldown_until = now + timedelta(hours=12)
            elif exc.kind == "quota":
                conn.status = "quota_exhausted"
                conn.cooldown_until = now + timedelta(hours=1)
            elif exc.retryable:
                conn.status = "degraded"
                delay = exc.retry_after or min(300.0, 15.0 * (2 ** (failures - 1)))
                conn.cooldown_until = now + timedelta(seconds=delay)

    @staticmethod
    def _store_cache(key: str, task: TaskSpec, res: HubResult) -> None:
        with session_scope() as db:
            entry = db.get(LLMCache, key)
            payload = {"text": res.text, "provider_type": res.provider_type, "model": res.model,
                       "connection_name": res.connection_name}
            expires = utcnow() + timedelta(seconds=task.cache_ttl_s or 0)
            if entry is None:
                db.add(LLMCache(key=key, task=task.name, response=payload, expires_at=expires))
            else:
                entry.response = payload
                entry.expires_at = expires


hub = ModelHub()


# ---------------------------------------------------------------------------
# Connection management (used by the API and tests)
# ---------------------------------------------------------------------------


def sync_models(db: Session, conn: ProviderConnection, discovered: list[tuple[str, int | None, dict]]) -> int:
    """Upsert discovered models with inferred profiles. Returns count added."""
    existing = {m.model_id: m for m in conn.models}
    added = 0
    for model_id, context, caps in discovered:
        prof = catalog.profile(conn.provider_type, model_id, is_local=conn.is_local, context_window=context)
        capabilities = {"chat": prof.chat, "vision": prof.vision, "json": prof.json, "embeddings": prof.embeddings}
        capabilities.update({k: v for k, v in (caps or {}).items() if k in capabilities})
        if model_id in existing:
            row = existing[model_id]
            if row.capabilities.get("user_override"):
                continue
            row.capabilities = {**capabilities, **{k: v for k, v in row.capabilities.items() if k == "user_override"}}
            row.context_window = row.context_window or prof.context_window
            continue
        db.add(ProviderModel(connection=conn, model_id=model_id, capabilities=capabilities,
                             context_window=prof.context_window, quality_tier=prof.quality_tier,
                             price_in_per_mtok=prof.price_in, price_out_per_mtok=prof.price_out,
                             price_source=prof.price_source))
        added += 1
    db.flush()
    return added


def test_connection(db: Session, conn: ProviderConnection, *, probe_model: str | None = None) -> dict[str, Any]:
    """Verify credentials (model listing + a one-token chat) and refresh models."""
    report: dict[str, Any] = {"ok": False, "models_discovered": 0, "chat_ok": None, "error": None}
    try:
        api_key = read_secret(db, conn.secret_id) if conn.secret_id else None
    except (SecretsUnavailable, SecretDecryptionError) as exc:
        report["error"] = f"Cannot read stored key: {exc}"
        conn.status = "error"
        conn.last_error = report["error"]
        return report
    adapter = build_adapter(conn.provider_type, api_key=api_key, base_url=conn.base_url, config=conn.config)
    try:
        discovered = adapter.list_models()
        filtered = [(d.model_id, d.context_window, d.capabilities) for d in discovered]
        if conn.provider_type == "openai":
            filtered = [d for d in filtered if re.match(r"^(gpt-|o\d|chatgpt-|text-embedding)", d[0])]
        if conn.provider_type == "gemini":
            filtered = [d for d in filtered if d[2].get("chat", True) and "gemini" in d[0]]
        report["models_discovered"] = sync_models(db, conn, filtered)
        report["model_count"] = len(filtered)
    except ProviderError as exc:
        if exc.kind == "auth":
            conn.status = "invalid_credentials"
            conn.last_error = str(exc)
            conn.last_tested_at = utcnow()
            report["error"] = str(exc)
            return report
        report["discovery_error"] = f"{exc.kind}: {exc}"
        suggested = catalog.SUGGESTED_MODELS.get(conn.provider_type, [])
        if suggested and not conn.models:
            sync_models(db, conn, [(m, None, {}) for m in suggested])
    db.refresh(conn)
    chat_models = [m for m in conn.models if m.enabled and m.capabilities.get("chat", True)]
    model_id = probe_model or (sorted(chat_models, key=lambda m: (m.quality_tier, m.model_id))[0].model_id if chat_models else None)
    if model_id is None:
        report["error"] = report.get("discovery_error") or "No chat-capable models found on this connection"
        conn.status = "error"
        conn.last_error = report["error"]
        conn.last_tested_at = utcnow()
        return report
    try:
        result = adapter.chat([ChatMessage("user", "Reply with the single word OK.")], model_id,
                              ChatParams(temperature=0.0, max_tokens=8, timeout_s=60))
        report["chat_ok"] = True
        report["probe_model"] = model_id
        report["probe_reply"] = result.text.strip()[:40]
        report["ok"] = True
        conn.status = "ok"
        conn.last_error = None
        conn.cooldown_until = None
    except ProviderError as exc:
        report["chat_ok"] = False
        report["error"] = f"{exc.kind}: {exc}"
        conn.status = "invalid_credentials" if exc.kind == "auth" else "error"
        conn.last_error = report["error"]
    conn.last_tested_at = utcnow()
    db.flush()
    return report
