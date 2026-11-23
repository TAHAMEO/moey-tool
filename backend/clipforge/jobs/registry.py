"""Job handler registry and the context object handlers receive."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import queue


class Suspend(Exception):
    """Workflow is waiting (for child jobs or for a human decision)."""

    def __init__(self, label: str, info: dict[str, Any] | None = None):
        super().__init__(label)
        self.label = label
        self.info = info or {}


class Cancelled(Exception):
    pass


class PauseRequested(Exception):
    pass


class RetryableError(Exception):
    """Transient failure; the queue retries with backoff."""


class PermanentError(Exception):
    """Failure that retrying cannot fix. ``user_message`` is shown in the UI."""

    def __init__(self, user_message: str, *, code: str = "failed", label: str = "Failed",
                 details: dict[str, Any] | None = None):
        super().__init__(user_message)
        self.user_message = user_message
        self.code = code
        self.label = label
        self.details = details or {}


@dataclass
class HandlerSpec:
    fn: Callable[[JobContext], dict[str, Any] | None]
    queue: str
    needs_secrets: bool


HANDLERS: dict[str, HandlerSpec] = {}


def handler(job_type: str, *, queue: str = "default", needs_secrets: bool = False):
    def decorator(fn):
        HANDLERS[job_type] = HandlerSpec(fn=fn, queue=queue, needs_secrets=needs_secrets)
        return fn

    return decorator


@dataclass
class JobContext:
    job_id: uuid.UUID
    type: str
    payload: dict[str, Any]
    attempt: int
    checkpoint: dict[str, Any]
    resume_value: Any
    project_id: uuid.UUID | None
    user_id: uuid.UUID | None
    worker_id: str = ""
    flags: dict[str, bool] = field(default_factory=lambda: {"cancel": False, "pause": False, "lost": False})
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ------------------------------------------------------------ reporting
    def progress(self, fraction: float | None = None, *, label: str | None = None, stage: str | None = None,
                 message: str | None = None, data: dict | None = None, detail: dict | None = None) -> None:
        queue.set_progress(self.job_id, progress=fraction, label=label, stage=stage, message=message, data=data,
                           detail=detail)

    def event(self, message: str, *, stage: str | None = None, data: dict | None = None) -> None:
        queue.set_progress(self.job_id, message=message, stage=stage, data=data)

    def save_checkpoint(self, **values: Any) -> None:
        self.checkpoint.update(values)
        queue.save_checkpoint(self.job_id, values)

    # ------------------------------------------------------------- control
    def cancelled(self) -> bool:
        return self.flags.get("cancel", False) or self.flags.get("lost", False)

    def check(self) -> None:
        """Call at safe points: raises when the user cancelled or paused."""
        if self.cancelled():
            raise Cancelled()
        if self.flags.get("pause"):
            raise PauseRequested()
