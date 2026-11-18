"""Global pause, emergency stop and integration kill-switches (Part 21)."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from ..security.audit import audit
from . import app_settings

ACTIONS = {"pause", "resume", "emergency_stop", "clear_emergency", "disable_integration", "enable_integration"}

#: Every switch here is enforced somewhere: ``publish:*`` in the publishing
#: service, ``provider:*`` in the Model Hub router, ``source:*`` in the access
#: resolver. Unknown names are rejected so a typo cannot look like a disabled
#: integration that is in fact still running.
INTEGRATIONS = {
    "publish:youtube": "Publishing to YouTube",
    "publish:instagram": "Publishing to Instagram",
    "source:youtube": "Fetching YouTube links",
    "source:instagram": "Fetching Instagram links",
    "source:gdrive": "Fetching Google Drive links",
    "source:direct": "Fetching direct file links",
    "provider:openai": "OpenAI models",
    "provider:gemini": "Google Gemini models",
    "provider:anthropic": "Anthropic Claude models",
    "provider:openai_compatible": "OpenAI-compatible endpoints",
    "provider:ollama": "Ollama (local) models",
}


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
        if integration not in INTEGRATIONS:
            raise ValueError(f"Unknown integration {integration!r}")
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


def integration_disabled_by_owner(db: Session, name: str) -> bool:
    return name in app_settings.get_section(db, "control").disabled_integrations
