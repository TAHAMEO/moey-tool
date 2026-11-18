"""Structured-ish logging with secret redaction."""

from __future__ import annotations

import logging
import re
import sys

_SECRET_PATTERNS = [
    re.compile(r"(sk-[A-Za-z0-9_\-]{8})[A-Za-z0-9_\-]+"),
    re.compile(r"(AIza[0-9A-Za-z_\-]{6})[0-9A-Za-z_\-]+"),
    re.compile(r"(?i)(access_token=|refresh_token=|key=|x-api-key:\s*|Bearer\s+)([^\s&\"']{6})[^\s&\"']*"),
]


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = message
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub(lambda m: "".join(g for g in m.groups() if g) + "…[redacted]", redacted)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


_configured = False


def configure_logging(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "urllib3", "botocore", "asyncio", "multipart"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True
