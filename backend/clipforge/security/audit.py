"""Append-only audit trail of security-relevant actions."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from ..db.models import AuditLog

log = logging.getLogger("clipforge.audit")

_REDACT_KEYS = {"api_key", "password", "token", "access_token", "refresh_token", "secret", "client_secret"}


def _redact(details: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in details.items():
        if key.lower() in _REDACT_KEYS:
            clean[key] = "[redacted]"
        elif isinstance(value, dict):
            clean[key] = _redact(value)
        else:
            clean[key] = value
    return clean


def audit(
    db: Session,
    action: str,
    *,
    user_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: str | uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    entry = AuditLog(
        user_id=user_id,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        details=_redact(details or {}),
        ip=ip,
    )
    db.add(entry)
    log.info("audit action=%s target=%s:%s user=%s", action, target_type, target_id, user_id)
