"""Permanent memory (Part 10) built on Mem0.

* Vectors live in the CLIPFORGE PostgreSQL database (pgvector collection
  ``clipforge_memories``), so memory survives restarts and is included in
  database backups. Mem0's change history is a SQLite file on the data volume.
* Embeddings come from the local hybrid embedder: no API cost.
* Fact extraction/consolidation (``infer=True``) uses whatever model the Model
  Hub routes to (local-first). With no model available, memories are stored
  verbatim (``infer=False``) — the feature degrades, it does not disappear.
* Memory scopes: ``user_id=<owner id>`` for preferences/facts about the owner,
  ``agent_id="clipforge-brain"`` for knowledge the Brain has learned.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import UTC, datetime
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from ..config import get_settings
from ..db.base import session_scope
from ..providers.base import ChatMessage, NoProviderAvailable
from ..providers.embeddings import DIMS, get_embedder
from ..providers.hub import TaskSpec, hub

os.environ.setdefault("MEM0_TELEMETRY", "False")

log = logging.getLogger(__name__)

COLLECTION = "clipforge_memories"
BRAIN_AGENT = "clipforge-brain"


class LocalEmbeddings(Embeddings):
    """LangChain wrapper around the zero-cost local hybrid embedder."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return get_embedder().embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return get_embedder().embed_one(text)


class HubChatModel(BaseChatModel):
    """LangChain chat model that delegates to the CLIPFORGE Model Hub."""

    task_name: str = "memory_extract"
    quality: int = 1

    @property
    def _llm_type(self) -> str:
        return "clipforge-hub"

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None,
                  run_manager: CallbackManagerForLLMRun | None = None, **kwargs: Any) -> ChatResult:
        converted: list[ChatMessage] = []
        for message in messages:
            role = {"system": "system", "human": "user", "ai": "assistant"}.get(message.type, "user")
            content = message.content if isinstance(message.content, str) else json.dumps(message.content)
            converted.append(ChatMessage(role, content))
        response_format = kwargs.get("response_format") or {}
        # Extraction runs in a background job, and Mem0's prompt is long (~8k tokens): a CPU-only
        # local model can need minutes, so allow them.
        task = TaskSpec(self.task_name, quality=self.quality, needs_json=response_format.get("type") == "json_object",
                        temperature=0.0, max_tokens=1200, cache_ttl_s=7 * 24 * 3600, max_providers=2, timeout_s=420.0)
        result = hub.complete(task, converted)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=result.text))])


class MemoryService:
    def __init__(self) -> None:
        settings = get_settings()
        history_dir = settings.data_dir / "mem0"
        history_dir.mkdir(parents=True, exist_ok=True)
        # Mem0 creates its state directory (default ~/.mem0) when it is first imported.
        # Keep it in the data directory: the home directory may not be writable (containers)
        # and the state must survive restarts and be included in backups.
        os.environ.setdefault("MEM0_DIR", str(history_dir))
        from mem0 import Memory

        config = {
            "vector_store": {
                "provider": "pgvector",
                "config": {
                    "connection_string": settings.psycopg_dsn,
                    "collection_name": COLLECTION,
                    "embedding_model_dims": DIMS,
                    "hnsw": True,
                },
            },
            "embedder": {"provider": "langchain", "config": {"model": LocalEmbeddings()}},
            "llm": {"provider": "langchain", "config": {"model": HubChatModel()}},
            "history_db_path": str(history_dir / "history.db"),
        }
        self._memory = Memory.from_config(config)
        self._lock = threading.Lock()

    @staticmethod
    def llm_available() -> bool:
        with session_scope() as db:
            candidates, _ = hub.candidates(db, TaskSpec("memory_extract", quality=1), 2000)
        return bool(candidates)

    @staticmethod
    def _scope(user_id: str | None, agent: bool) -> dict[str, str]:
        return {"agent_id": BRAIN_AGENT} if agent else {"user_id": user_id or "owner"}

    # ----------------------------------------------------------------- write
    def remember(self, text: str, *, user_id: str | None = None, agent: bool = False, category: str = "fact",
                 metadata: dict[str, Any] | None = None, infer: bool | None = None) -> list[dict[str, Any]]:
        """Store a memory. Returns the Mem0 events (ADD/UPDATE/DELETE/NONE)."""
        meta = {"category": category, "stored_at": datetime.now(UTC).isoformat(), **(metadata or {})}
        use_llm = self.llm_available() if infer is None else infer
        with self._lock:
            if use_llm:
                try:
                    out = self._memory.add([{"role": "user", "content": text}], metadata=meta, infer=True,
                                           **self._scope(user_id, agent))
                    events = out.get("results", []) if isinstance(out, dict) else out
                    if events:
                        return events
                    log.info("Memory extraction returned no facts; storing verbatim")
                except NoProviderAvailable:
                    log.info("No model for memory extraction; storing verbatim")
                except Exception as exc:  # noqa: BLE001 - malformed LLM output inside Mem0, etc.
                    log.warning("Mem0 inference failed (%s); storing verbatim", exc)
            out = self._memory.add([{"role": "user", "content": text}], metadata=meta, infer=False,
                                   **self._scope(user_id, agent))
            return out.get("results", []) if isinstance(out, dict) else out

    def refine(self, text: str, *, verbatim_id: str, user_id: str | None = None, agent: bool = False,
               category: str = "fact") -> list[dict[str, Any]]:
        """Let the LLM turn a memory stored verbatim into clean facts (Mem0 ADD/UPDATE/DELETE).

        The verbatim copy is removed only when the facts were stored as separate entries;
        if the model updated the verbatim entry itself, or found nothing new, it stays.
        """
        meta = {"category": category, "stored_at": datetime.now(UTC).isoformat(), "refined_from": verbatim_id}
        with self._lock:
            out = self._memory.add([{"role": "user", "content": text}], metadata=meta, infer=True,
                                   **self._scope(user_id, agent))
        events = out.get("results", []) if isinstance(out, dict) else out
        written = {e.get("id") for e in events if e.get("event") in ("ADD", "UPDATE")}
        removed = {e.get("id") for e in events if e.get("event") == "DELETE"}
        if written and verbatim_id not in written and verbatim_id not in removed:
            self.delete(verbatim_id)
        return events

    def update(self, memory_id: str, text: str) -> dict[str, Any]:
        with self._lock:
            return self._memory.update(memory_id, text)

    def delete(self, memory_id: str) -> None:
        with self._lock:
            self._memory.delete(memory_id)

    def delete_all(self, *, user_id: str | None = None, agent: bool = False) -> None:
        with self._lock:
            self._memory.delete_all(**self._scope(user_id, agent))

    # ------------------------------------------------------------------ read
    def recall(self, query: str, *, user_id: str | None = None, agent: bool = False, limit: int = 5,
               min_score: float = 0.25) -> list[dict[str, Any]]:
        out = self._memory.search(query, top_k=limit, filters=self._scope(user_id, agent), threshold=min_score)
        results = out.get("results", []) if isinstance(out, dict) else out
        return [{**item, "similarity": round(float(item["score"]), 3) if item.get("score") is not None else None}
                for item in results]

    def list(self, *, user_id: str | None = None, agent: bool = False, limit: int = 500) -> list[dict[str, Any]]:
        out = self._memory.get_all(filters=self._scope(user_id, agent), top_k=limit)
        return out.get("results", []) if isinstance(out, dict) else out

    def history(self, memory_id: str) -> list[dict[str, Any]]:
        return self._memory.history(memory_id)

    def export(self, *, user_id: str | None = None) -> dict[str, Any]:
        return {
            "exported_at": datetime.now(UTC).isoformat(),
            "embedder": get_embedder().id,
            "user_memories": self.list(user_id=user_id),
            "brain_memories": self.list(agent=True),
        }


_service: MemoryService | None = None
_service_lock = threading.Lock()


def get_memory() -> MemoryService:
    global _service
    with _service_lock:
        if _service is None:
            _service = MemoryService()
        return _service


def reset_memory() -> None:
    global _service
    with _service_lock:
        _service = None
