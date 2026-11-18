"""HTTP helper for official platform APIs: retries with exponential backoff + jitter."""

from __future__ import annotations

import logging
import random
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


class PlatformError(RuntimeError):
    def __init__(self, platform: str, message: str, *, status: int | None = None, reason: str | None = None,
                 retryable: bool = False, payload: Any = None):
        super().__init__(message)
        self.platform = platform
        self.status = status
        self.reason = reason
        self.retryable = retryable
        self.payload = payload


def request(platform: str, method: str, url: str, *, max_attempts: int = 5, timeout: float = 60.0,
            retry_on: set[int] = RETRY_STATUS, **kwargs) -> httpx.Response:
    delay = 1.0
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout, connect=15.0)) as client:
                response = client.request(method, url, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if attempt == max_attempts:
                raise PlatformError(platform, f"Could not reach {platform}: {exc.__class__.__name__}", retryable=True) from exc
        else:
            if response.status_code not in retry_on or attempt == max_attempts:
                return response
            retry_after = response.headers.get("retry-after")
            if retry_after and retry_after.isdigit():
                delay = max(delay, float(retry_after))
            log.warning("%s %s -> %s; retrying (attempt %d)", platform, url.split("?")[0], response.status_code, attempt)
        time.sleep(min(60.0, delay) * (0.75 + random.random() / 2))
        delay *= 2
    raise PlatformError(platform, f"Request failed: {last_exc}", retryable=True)


def google_error(response: httpx.Response) -> PlatformError:
    try:
        error = response.json().get("error", {})
    except ValueError:
        error = {}
    reasons = [e.get("reason") for e in error.get("errors", []) if isinstance(e, dict)] if isinstance(error, dict) else []
    reason = reasons[0] if reasons else (error.get("status") if isinstance(error, dict) else None)
    message = error.get("message") if isinstance(error, dict) else str(error)
    retryable = response.status_code in RETRY_STATUS or reason in ("backendError", "rateLimitExceeded", "userRateLimitExceeded")
    return PlatformError("youtube", f"YouTube API error {response.status_code}: {message or response.text[:300]}",
                         status=response.status_code, reason=reason, retryable=retryable, payload=error)


def meta_error(response: httpx.Response) -> PlatformError:
    try:
        error = response.json().get("error", {})
    except ValueError:
        error = {}
    code = error.get("code")
    message = error.get("message") or response.text[:300]
    # Meta: 190 invalid/expired token, 10/200-299 permission, 4/17/32/613 rate limits, 2/1 transient.
    retryable = code in (1, 2, 4, 17, 32, 341, 613) or response.status_code >= 500
    reason = "token" if code == 190 else "permission" if code in (10,) or (isinstance(code, int) and 200 <= code < 300) else str(code)
    return PlatformError("instagram", f"Instagram API error {response.status_code}: {message}", status=response.status_code,
                         reason=reason, retryable=retryable, payload=error)
