"""Global pause, emergency stop and integration kill-switches (Part 21)."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from ..security.audit import audit
from . import app_settings

ACTIONS = {"pause", "resume", "emergency_stop", "clear_emergency", "disable_integration", "enable_integration"}


def apply(db: Session, action: str, *, user_id: uuid.UUID | None, ip: str | None = None,
          integration: str | None = None) -> dict:
    if action not in ACTIONS:
        raise ValueError(f"Unknown control action {action!r}")
    current = app_settings.get_section(db, "control")
    patch: dict = {}
    cancelled = 0
    if action == "pause":
        patch["paused"] = True
    elif action == "resume":
        patch["paused"] = False
    elif action == "emergency_stop":
        patch["emergency_stop"] = True
        patch["paused"] = True
        from ..jobs import queue

        cancelled = queue.cancel_all_active(db, reason="Emergency stop")
    elif action == "clear_emergency":
        patch["emergency_stop"] = False
    elif action in {"disable_integration", "enable_integration"}:
        if not integration:
            raise ValueError("Specify the integration name")
        items = set(current.disabled_integrations)
        (items.add if action == "disable_integration" else items.discard)(integration)
        patch["disabled_integrations"] = sorted(items)
    updated = app_settings.update_section(db, "control", patch, user_id=user_id, ip=ip)
    audit(db, f"control.{action}", user_id=user_id, details={"cancelled_jobs": cancelled, "integration": integration}, ip=ip)
    return {**updated.model_dump(), "cancelled_jobs": cancelled}


def is_paused(db: Session) -> bool:
    control = app_settings.get_section(db, "control")
    return control.paused or control.emergency_stop


def integration_enabled(db: Session, name: str) -> bool:
    control = app_settings.get_section(db, "control")
    return name not in control.disabled_integrations and not control.emergency_stop
