"""Local AI jobs: downloading a model into Ollama on this PC (with progress)."""

from __future__ import annotations

from ..db.base import session_scope
from ..ingestion.acquire import TransferMeter
from ..jobs.registry import JobContext, PermanentError, RetryableError, handler
from ..providers import local_ai


@handler("ai.pull_model", queue="default")
def pull_model(ctx: JobContext) -> dict:
    name = ctx.payload["model"]
    meter = TransferMeter()
    ctx.progress(0.0, label="Downloading the AI model", stage="download", message=f"Downloading {name} into Ollama")

    def report(done: int, total: int, status: str) -> None:
        ctx.check()
        if total:
            ctx.progress(min(0.99, done / total), label="Downloading the AI model", detail=meter.detail(done, total))
        elif status:
            ctx.progress(label=f"Downloading the AI model ({status})")

    try:
        local_ai.pull(name, on_progress=report, cancelled=ctx.cancelled)
    except local_ai.LocalAIError as exc:
        if "not running" in str(exc):
            raise RetryableError(str(exc)) from exc
        raise PermanentError(str(exc), code="model_download") from exc
    ctx.progress(1.0, label="Connecting the model", stage="connect")
    with session_scope() as db:
        conn, test = local_ai.connect(db, "ollama")
        ok = bool(test and test.get("ok"))
        models = sorted(m.model_id for m in conn.models)
    return {"_label": "AI model ready" if ok else "Downloaded", "model": name, "connection_ok": ok, "models": models}
