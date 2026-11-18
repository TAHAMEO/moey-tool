"""Provider-neutral types for the AI Model Connection Hub (Part 8)."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any, Literal

ErrorKind = Literal[
    "auth", "rate_limit", "timeout", "unavailable", "bad_request", "unsupported",
    "quota", "not_found", "budget", "invalid_output", "unknown",
]

RETRYABLE: set[str] = {"rate_limit", "timeout", "unavailable"}


class ProviderError(Exception):
    def __init__(self, kind: ErrorKind, message: str, *, status: int | None = None,
                 retry_after: float | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE


class NoProviderAvailable(RuntimeError):
    """No connected provider can serve the request (or budget forbids it).

    Callers are expected to fall back to their local implementation.
    """

    def __init__(self, message: str, attempts: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.attempts = attempts or []


@dataclass
class ImagePart:
    data: bytes
    mime: str = "image/jpeg"

    @property
    def b64(self) -> str:
        return base64.b64encode(self.data).decode()


@dataclass
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str
    images: list[ImagePart] = field(default_factory=list)


@dataclass
class ChatParams:
    temperature: float = 0.2
    max_tokens: int = 800
    json_mode: bool = False
    json_schema: dict[str, Any] | None = None
    timeout_s: float = 90.0


@dataclass
class ChatResult:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str | None = None
    latency_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class DiscoveredModel:
    model_id: str
    context_window: int | None = None
    capabilities: dict[str, Any] = field(default_factory=dict)


def classify_http_error(status: int, body: str, headers: dict[str, str] | None = None) -> ProviderError:
    text = (body or "")[:600]
    lowered = text.lower()
    retry_after = None
    if headers:
        value = headers.get("retry-after") or headers.get("Retry-After")
        if value:
            try:
                retry_after = float(value)
            except ValueError:
                retry_after = None
    if status in (401, 403):
        if "quota" in lowered or "billing" in lowered:
            return ProviderError("quota", f"Quota/billing problem: {text}", status=status)
        return ProviderError("auth", "The API key was rejected (invalid, expired or lacking permission).", status=status)
    if status == 402 or "insufficient_quota" in lowered or "credit balance" in lowered:
        return ProviderError("quota", f"Provider account has no remaining quota: {text}", status=status)
    if status == 429:
        if "quota" in lowered and "rate" not in lowered:
            return ProviderError("quota", f"Quota exhausted: {text}", status=status, retry_after=retry_after)
        return ProviderError("rate_limit", "Rate limited by provider.", status=status, retry_after=retry_after)
    if status == 404:
        return ProviderError("not_found", f"Model or endpoint not found: {text}", status=status)
    if status in (400, 422):
        if "image" in lowered and ("support" in lowered or "invalid" in lowered):
            return ProviderError("unsupported", f"Unsupported input: {text}", status=status)
        if "context" in lowered and ("length" in lowered or "window" in lowered or "too long" in lowered):
            return ProviderError("unsupported", f"Input too long for model: {text}", status=status)
        return ProviderError("bad_request", f"Request rejected: {text}", status=status)
    if status in (408, 504):
        return ProviderError("timeout", "Provider timed out.", status=status)
    if status >= 500:
        return ProviderError("unavailable", f"Provider error {status}.", status=status, retry_after=retry_after)
    return ProviderError("unknown", f"Unexpected HTTP {status}: {text}", status=status)
