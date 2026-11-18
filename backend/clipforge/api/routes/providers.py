"""AI Connections: add, test, configure and remove model providers."""

from __future__ import annotations

import ipaddress
import socket
import uuid
from datetime import timedelta
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ...db.base import get_db, utcnow
from ...db.models import AIUsage, ProviderConnection, ProviderModel
from ...providers import catalog
from ...providers.hub import is_local_url, test_connection
from ...security.audit import audit
from ...security.crypto import SecretsUnavailable, delete_secret, store_secret, update_secret
from ..deps import Principal, admin, client_ip, viewer

router = APIRouter(prefix="/providers", tags=["providers"])

ProviderType = Literal["openai", "gemini", "anthropic", "openai_compatible", "ollama"]
DEFAULT_BASE = {"ollama": "http://localhost:11434/v1"}


class ProviderCreate(BaseModel):
    provider_type: ProviderType
    name: str | None = Field(None, max_length=120)
    api_key: str | None = Field(None, max_length=500)
    base_url: str | None = Field(None, max_length=500)
    test: bool = True


class ProviderUpdate(BaseModel):
    name: str | None = Field(None, max_length=120)
    api_key: str | None = Field(None, max_length=500)
    base_url: str | None = Field(None, max_length=500)
    enabled: bool | None = None


class ModelUpdate(BaseModel):
    enabled: bool | None = None
    quality_tier: int | None = Field(None, ge=1, le=3)
    price_in_per_mtok: float | None = Field(None, ge=0)
    price_out_per_mtok: float | None = Field(None, ge=0)
    vision: bool | None = None


def validate_base_url(url: str) -> str:
    """Provider endpoints may be local (Ollama, LM Studio) but never link-local/metadata."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise HTTPException(400, {"code": "base_url", "message": "Base URL must be an http(s) URL."})
    if parts.username or parts.password:
        raise HTTPException(400, {"code": "base_url", "message": "Do not put credentials in the URL."})
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443)
        addresses = {info[4][0] for info in infos}
    except socket.gaierror:
        addresses = set()
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified or str(ip) in {"169.254.169.254", "fd00:ec2::254"}:
            raise HTTPException(400, {"code": "base_url", "message": "That address is not allowed."})
    return url.rstrip("/")


def _serialize(db: Session, conn: ProviderConnection) -> dict:
    since = utcnow() - timedelta(days=30)
    stats = db.execute(
        select(func.count(), func.coalesce(func.sum(AIUsage.cost_usd), 0.0),
               func.coalesce(func.sum(AIUsage.input_tokens + AIUsage.output_tokens), 0))
        .where(AIUsage.connection_id == conn.id, AIUsage.created_at >= since)
    ).one()
    hint = None
    if conn.secret_id:
        from ...db.models import Secret

        secret = db.get(Secret, conn.secret_id)
        hint = secret.hint if secret else None
    return {
        "id": str(conn.id),
        "provider_type": conn.provider_type,
        "provider_label": catalog.PROVIDER_LABELS.get(conn.provider_type, conn.provider_type),
        "name": conn.name,
        "base_url": conn.base_url,
        "enabled": conn.enabled,
        "is_local": conn.is_local,
        "status": conn.status,
        "last_error": conn.last_error,
        "last_tested_at": conn.last_tested_at,
        "cooldown_until": conn.cooldown_until,
        "key_hint": hint,
        "usage_30d": {"calls": stats[0], "cost_usd": round(float(stats[1]), 4), "tokens": int(stats[2])},
        "models": [
            {
                "id": str(m.id), "model_id": m.model_id, "enabled": m.enabled, "quality_tier": m.quality_tier,
                "capabilities": m.capabilities, "context_window": m.context_window,
                "price_in_per_mtok": m.price_in_per_mtok, "price_out_per_mtok": m.price_out_per_mtok,
                "price_source": m.price_source, "calls": m.calls,
                "latency_ms": round(m.latency_ms_ema) if m.latency_ms_ema else None,
                "success_rate": round(m.success_ema, 3),
            }
            for m in conn.models
        ],
    }


def _load(db: Session, conn_id: uuid.UUID) -> ProviderConnection:
    conn = db.execute(
        select(ProviderConnection).options(selectinload(ProviderConnection.models)).where(ProviderConnection.id == conn_id)
    ).scalar_one_or_none()
    if conn is None:
        raise HTTPException(404, {"code": "not_found", "message": "Connection not found."})
    return conn


@router.get("")
def list_providers(db: Session = Depends(get_db), _: Principal = Depends(viewer)) -> dict:
    rows = db.execute(
        select(ProviderConnection).options(selectinload(ProviderConnection.models)).order_by(ProviderConnection.created_at)
    ).scalars().all()
    return {
        "connections": [_serialize(db, c) for c in rows],
        "available_types": [{"type": k, "label": v, "needs_key": k not in ("ollama", "openai_compatible"),
                             "needs_base_url": k in ("ollama", "openai_compatible")}
                            for k, v in catalog.PROVIDER_LABELS.items()],
    }


@router.post("")
def add_provider(body: ProviderCreate, request: Request, db: Session = Depends(get_db),
                 principal: Principal = Depends(admin)) -> dict:
    needs_key = body.provider_type in ("openai", "gemini", "anthropic")
    if needs_key and not (body.api_key and body.api_key.strip()):
        raise HTTPException(400, {"code": "api_key", "message": "This provider needs an API key."})
    base_url = body.base_url or DEFAULT_BASE.get(body.provider_type)
    if body.provider_type == "openai_compatible" and not base_url:
        raise HTTPException(400, {"code": "base_url", "message": "Enter the endpoint base URL (ending in /v1)."})
    if base_url:
        base_url = validate_base_url(base_url)
    conn = ProviderConnection(
        provider_type=body.provider_type,
        name=body.name or catalog.PROVIDER_LABELS[body.provider_type],
        base_url=base_url,
        is_local=is_local_url(base_url),
    )
    db.add(conn)
    db.flush()
    if body.api_key and body.api_key.strip():
        try:
            conn.secret_id = store_secret(db, f"provider:{conn.id}", body.api_key.strip()).id
        except SecretsUnavailable as exc:
            raise HTTPException(500, {"code": "secrets", "message": "Server has no encryption key configured."}) from exc
    audit(db, "provider.add", user_id=principal.id, target_type="provider", target_id=conn.id,
          details={"type": body.provider_type, "base_url": base_url}, ip=client_ip(request))
    db.flush()
    report = test_connection(db, conn) if body.test else None
    db.refresh(conn)
    return {"connection": _serialize(db, _load(db, conn.id)), "test": report}


@router.patch("/{conn_id}")
def update_provider(conn_id: uuid.UUID, body: ProviderUpdate, request: Request, db: Session = Depends(get_db),
                    principal: Principal = Depends(admin)) -> dict:
    conn = _load(db, conn_id)
    if body.name is not None:
        conn.name = body.name
    if body.enabled is not None:
        conn.enabled = body.enabled
    if body.base_url is not None:
        conn.base_url = validate_base_url(body.base_url)
        conn.is_local = is_local_url(conn.base_url)
    if body.api_key:
        from ...db.models import Secret

        if conn.secret_id and (secret := db.get(Secret, conn.secret_id)):
            update_secret(db, secret, body.api_key.strip())
        else:
            conn.secret_id = store_secret(db, f"provider:{conn.id}", body.api_key.strip()).id
        conn.status = "untested"
        conn.cooldown_until = None
    audit(db, "provider.update", user_id=principal.id, target_type="provider", target_id=conn.id,
          details={k: v for k, v in body.model_dump(exclude_none=True).items() if k != "api_key"}, ip=client_ip(request))
    db.flush()
    return _serialize(db, conn)


@router.post("/{conn_id}/test")
def retest(conn_id: uuid.UUID, db: Session = Depends(get_db), _: Principal = Depends(admin)) -> dict:
    conn = _load(db, conn_id)
    report = test_connection(db, conn)
    return {"connection": _serialize(db, _load(db, conn_id)), "test": report}


@router.patch("/{conn_id}/models/{model_row_id}")
def update_model(conn_id: uuid.UUID, model_row_id: uuid.UUID, body: ModelUpdate, db: Session = Depends(get_db),
                 _: Principal = Depends(admin)) -> dict:
    model = db.get(ProviderModel, model_row_id)
    if model is None or model.connection_id != conn_id:
        raise HTTPException(404, {"code": "not_found", "message": "Model not found."})
    data = body.model_dump(exclude_none=True)
    if "vision" in data:
        model.capabilities = {**model.capabilities, "vision": data.pop("vision"), "user_override": True}
    if "price_in_per_mtok" in data or "price_out_per_mtok" in data:
        model.price_source = "owner"
    for key, value in data.items():
        setattr(model, key, value)
    db.flush()
    return {"ok": True}


@router.delete("/{conn_id}")
def remove_provider(conn_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
                    principal: Principal = Depends(admin)) -> dict:
    conn = _load(db, conn_id)
    secret_id = conn.secret_id
    db.delete(conn)
    db.flush()
    delete_secret(db, secret_id)
    audit(db, "provider.remove", user_id=principal.id, target_type="provider", target_id=conn_id, ip=client_ip(request))
    return {"ok": True}
