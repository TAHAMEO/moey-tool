"""Local AI on this PC: find a model server, connect it, download models.

CLIPFORGE talks to a model server running on the same computer — Ollama (recommended; free, for
Windows, macOS and Linux), LM Studio, Jan or a llama.cpp server — through the AI Connections it
already has, so the Brain can chat, improve clips and control CLIPFORGE with no API key, no cost
and nothing leaving the PC.

* :func:`detect` looks for those servers on 127.0.0.1 (a fraction of a second each).
* :func:`connect` adds (or refreshes) an AI Connection for one of them.
* :func:`pull` downloads a model into Ollama, reporting bytes as it goes.
* :func:`auto_connect` connects a running Ollama by itself when no AI model is connected yet.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import ProviderConnection

log = logging.getLogger(__name__)

#: Where each kind of local server listens by default.
SERVERS: tuple[dict[str, str], ...] = (
    {"kind": "ollama", "label": "Ollama", "provider_type": "ollama", "url": "http://127.0.0.1:11434"},
    {"kind": "lmstudio", "label": "LM Studio", "provider_type": "openai_compatible", "url": "http://127.0.0.1:1234"},
    {"kind": "jan", "label": "Jan", "provider_type": "openai_compatible", "url": "http://127.0.0.1:1337"},
    {"kind": "llamacpp", "label": "llama.cpp server", "provider_type": "openai_compatible", "url": "http://127.0.0.1:8080"},
)

#: Models that run on an ordinary PC without a graphics card and follow instructions well.
RECOMMENDED: tuple[dict[str, Any], ...] = (
    {"name": "qwen2.5:3b", "label": "Qwen 2.5 3B", "download_gb": 1.9, "memory_gb": 4,
     "note": "Recommended: quick on most PCs and good at following instructions."},
    {"name": "llama3.2:3b", "label": "Llama 3.2 3B", "download_gb": 2.0, "memory_gb": 4,
     "note": "Similar size; a good alternative."},
    {"name": "qwen2.5:7b", "label": "Qwen 2.5 7B", "download_gb": 4.7, "memory_gb": 8,
     "note": "Better answers; needs about 8 GB of free memory and is slower without a graphics card."},
)

OLLAMA_DOWNLOAD_URL = "https://ollama.com/download"
#: Ollama model names: "qwen2.5:3b", "library/llama3.2", "user/model:tag" — nothing else is sent.
_NAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-:/")


class LocalAIError(RuntimeError):
    pass


def _client(timeout: httpx.Timeout | float) -> httpx.Client:
    # Never through a proxy: these servers are on this PC (a system proxy would not reach them).
    return httpx.Client(timeout=timeout, trust_env=False)


def server(kind: str) -> dict[str, str]:
    for entry in SERVERS:
        if entry["kind"] == kind:
            found = dict(entry)
            if kind == "ollama":
                found["url"] = get_settings().ollama_url.rstrip("/")
            return found
    raise LocalAIError(f"Unknown local AI server: {kind}")


def base_url(kind: str) -> str:
    return server(kind)["url"] + "/v1"


def valid_model_name(name: str) -> bool:
    return 0 < len(name) <= 120 and set(name) <= _NAME_CHARS and not name.startswith(("/", ".")) and ".." not in name


def _probe(entry: dict[str, str], timeout: float) -> dict[str, Any]:
    found: dict[str, Any] = {"kind": entry["kind"], "label": entry["label"], "url": entry["url"], "running": False,
                             "models": [], "version": None}
    try:
        with _client(timeout) as client:
            if entry["kind"] == "ollama":
                tags = client.get(entry["url"] + "/api/tags")
                if tags.status_code != 200:
                    return found
                found["models"] = [{"name": m.get("name") or m.get("model"), "size": m.get("size"),
                                    "family": (m.get("details") or {}).get("family"),
                                    "parameters": (m.get("details") or {}).get("parameter_size")}
                                   for m in tags.json().get("models", []) if m.get("name") or m.get("model")]
                version = client.get(entry["url"] + "/api/version")
                if version.status_code == 200:
                    found["version"] = version.json().get("version")
            else:
                listing = client.get(entry["url"] + "/v1/models")
                if listing.status_code != 200:
                    return found
                found["models"] = [{"name": m.get("id"), "size": None, "family": None, "parameters": None}
                                   for m in listing.json().get("data", []) if m.get("id")]
    except (httpx.HTTPError, ValueError):
        return found
    found["running"] = True
    return found


def detect(timeout: float = 0.8) -> list[dict[str, Any]]:
    """Every known local server, with whether it answers and its models (probed in parallel)."""
    entries = [server(e["kind"]) for e in SERVERS]
    results: list[dict[str, Any] | None] = [None] * len(entries)

    def run(i: int) -> None:
        results[i] = _probe(entries[i], timeout)

    threads = [threading.Thread(target=run, args=(i,), daemon=True) for i in range(len(entries))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout + 2)
    return [r or {**entries[i], "running": False, "models": [], "version": None} for i, r in enumerate(results)]


def _same_server(url_a: str | None, url_b: str) -> bool:
    def norm(url: str | None) -> str:
        return (url or "").rstrip("/").replace("://localhost", "://127.0.0.1").removesuffix("/v1")

    return norm(url_a) == norm(url_b)


def find_connection(db: Session, kind: str) -> ProviderConnection | None:
    url = server(kind)["url"]
    for conn in db.execute(select(ProviderConnection)).scalars():
        if conn.is_local and _same_server(conn.base_url, url):
            return conn
    return None


def connect(db: Session, kind: str = "ollama", *, test: bool = True) -> tuple[ProviderConnection, dict | None]:
    """Add (or re-test and re-enable) the AI Connection for a local server. Returns it and the test report."""
    from .hub import test_connection

    entry = server(kind)
    conn = find_connection(db, kind)
    if conn is None:
        conn = ProviderConnection(provider_type=entry["provider_type"], name=f"{entry['label']} (this PC)",
                                  base_url=entry["url"] + "/v1", is_local=True, config={"local_kind": kind})
        db.add(conn)
        db.flush()
    conn.enabled = True
    report = test_connection(db, conn) if test else None
    db.flush()
    return conn, report


def chat_model_ready(db: Session) -> bool:
    """Is any enabled, working connection able to chat?"""
    for conn in db.execute(select(ProviderConnection).where(ProviderConnection.enabled.is_(True))).scalars():
        if conn.status in ("error", "invalid_credentials"):
            continue
        if any(m.enabled and m.capabilities.get("chat", True) for m in conn.models):
            return True
    return False


_last_auto = [0.0]
_auto_lock = threading.Lock()


def auto_connect(db: Session, *, every_s: float = 60.0) -> bool:
    """With no chat model connected yet, connect a running Ollama that has a model (at most once a
    minute; quick when nothing answers). Returns True when a connection was made or refreshed."""
    if not get_settings().local_ai_autoconnect:
        return False
    with _auto_lock:
        if time.monotonic() - _last_auto[0] < every_s:
            return False
        _last_auto[0] = time.monotonic()
    if chat_model_ready(db):
        return False
    found = _probe(server("ollama"), 0.6)
    if not found["running"] or not found["models"]:
        return False
    _conn, report = connect(db, "ollama")
    log.info("Connected the local Ollama server (%d model(s)): %s", len(found["models"]),
             (report or {}).get("error") or "ok")
    return bool(report and report.get("ok"))


def pull(name: str, *, on_progress: Callable[[int, int, str], None] | None = None,
         cancelled: Callable[[], bool] | None = None) -> None:
    """Download ``name`` into the local Ollama (``ollama pull``). ``on_progress(done, total, status)``
    is called as layers arrive (about twice a second)."""
    if not valid_model_name(name):
        raise LocalAIError("That is not a valid model name.")
    url = server("ollama")["url"] + "/api/pull"
    layers: dict[str, tuple[int, int]] = {}
    reported = 0.0
    try:
        with _client(httpx.Timeout(60.0, connect=5.0)) as client, \
                client.stream("POST", url, json={"model": name, "stream": True}) as response:
            if response.status_code >= 400:
                raise LocalAIError(f"Ollama refused the download (HTTP {response.status_code}): "
                                   f"{response.read().decode(errors='replace')[:200]}")
            for line in response.iter_lines():
                if cancelled and cancelled():
                    raise LocalAIError("Download cancelled.")
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("error"):
                    raise LocalAIError(f"Ollama: {event['error']}")
                status = str(event.get("status") or "")
                if event.get("digest") and event.get("total"):
                    layers[event["digest"]] = (int(event.get("completed") or 0), int(event["total"]))
                done = sum(d for d, _ in layers.values())
                total = sum(t for _, t in layers.values())
                if on_progress and (time.monotonic() - reported >= 0.5 or status == "success"):
                    reported = time.monotonic()
                    on_progress(done, total, status)
                if status == "success":
                    return
    except httpx.ConnectError as exc:
        raise LocalAIError("Ollama is not running on this PC. Install it from ollama.com and start it, then try "
                           "again.") from exc
    except httpx.HTTPError as exc:
        raise LocalAIError(f"The download from Ollama stopped: {exc.__class__.__name__}") from exc
    raise LocalAIError("Ollama ended the download without finishing it.")
