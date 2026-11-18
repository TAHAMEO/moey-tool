"""HTTP adapters for model providers.

Implemented directly on ``httpx`` (no vendor SDKs) so every provider reports
errors in the same taxonomy, usage is captured uniformly and new providers only
need one small class. All adapters are synchronous and stateless.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from .base import (
    ChatMessage,
    ChatParams,
    ChatResult,
    DiscoveredModel,
    ProviderError,
    classify_http_error,
)


def _post(url: str, *, headers: dict[str, str], body: dict[str, Any], timeout: float) -> tuple[dict, int]:
    started = time.monotonic()
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
            response = client.post(url, headers=headers, json=body)
    except httpx.TimeoutException as exc:
        raise ProviderError("timeout", f"Request timed out after {timeout:.0f}s") from exc
    except httpx.HTTPError as exc:
        raise ProviderError("unavailable", f"Could not reach provider: {exc.__class__.__name__}") from exc
    latency = int((time.monotonic() - started) * 1000)
    if response.status_code >= 400:
        raise classify_http_error(response.status_code, response.text, dict(response.headers))
    try:
        return response.json(), latency
    except ValueError as exc:
        raise ProviderError("unknown", "Provider returned non-JSON output") from exc


def _get(url: str, *, headers: dict[str, str], timeout: float = 20.0) -> dict:
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(url, headers=headers)
    except httpx.TimeoutException as exc:
        raise ProviderError("timeout", "Request timed out") from exc
    except httpx.HTTPError as exc:
        raise ProviderError("unavailable", f"Could not reach provider: {exc.__class__.__name__}") from exc
    if response.status_code >= 400:
        raise classify_http_error(response.status_code, response.text, dict(response.headers))
    return response.json()


class Adapter:
    provider_type = "base"
    supports_embeddings = False

    def __init__(self, *, api_key: str | None, base_url: str | None, config: dict[str, Any] | None = None):
        self.api_key = api_key
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self.config = config or {}

    default_base_url = ""

    def chat(self, messages: list[ChatMessage], model: str, params: ChatParams) -> ChatResult:
        raise NotImplementedError

    def list_models(self) -> list[DiscoveredModel]:
        raise NotImplementedError

    def embed(self, texts: list[str], model: str) -> list[list[float]]:
        raise ProviderError("unsupported", f"{self.provider_type} does not provide embeddings")


# ---------------------------------------------------------------------------
# OpenAI and OpenAI-compatible servers (OpenRouter, Groq, Together, DeepSeek,
# Mistral, vLLM, LM Studio, llama.cpp server, Ollama's /v1 endpoint, ...)
# ---------------------------------------------------------------------------


class OpenAICompatibleAdapter(Adapter):
    provider_type = "openai_compatible"
    default_base_url = "http://localhost:11434/v1"
    supports_embeddings = True
    #: Official OpenAI reasoning models reject max_tokens/temperature.
    uses_max_completion_tokens = False

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _message(self, message: ChatMessage) -> dict[str, Any]:
        if not message.images:
            return {"role": message.role, "content": message.content}
        parts: list[dict[str, Any]] = [{"type": "text", "text": message.content}]
        for image in message.images:
            parts.append({"type": "image_url", "image_url": {"url": f"data:{image.mime};base64,{image.b64}"}})
        return {"role": message.role, "content": parts}

    def _is_reasoning_model(self, model: str) -> bool:
        m = model.lower()
        return m.startswith(("o1", "o3", "o4", "gpt-5"))

    def chat(self, messages: list[ChatMessage], model: str, params: ChatParams) -> ChatResult:
        body: dict[str, Any] = {"model": model, "messages": [self._message(m) for m in messages]}
        reasoning = self.uses_max_completion_tokens and self._is_reasoning_model(model)
        if self.uses_max_completion_tokens:
            body["max_completion_tokens"] = params.max_tokens * (4 if reasoning else 1)
        else:
            body["max_tokens"] = params.max_tokens
        if not reasoning:
            body["temperature"] = params.temperature
        if params.json_schema is not None and self.config.get("supports_json_schema", True):
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "result", "schema": params.json_schema, "strict": False},
            }
        elif params.json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            data, latency = _post(
                f"{self.base_url}/chat/completions", headers=self._headers(), body=body, timeout=params.timeout_s
            )
        except ProviderError as exc:
            # Some compatible servers reject json_schema; retry once with json_object.
            if exc.kind == "bad_request" and "response_format" in body and body["response_format"]["type"] == "json_schema":
                body["response_format"] = {"type": "json_object"}
                data, latency = _post(
                    f"{self.base_url}/chat/completions", headers=self._headers(), body=body, timeout=params.timeout_s
                )
            else:
                raise
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError("unknown", "Provider returned no choices")
        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        usage = data.get("usage") or {}
        return ChatResult(
            text=content,
            model=data.get("model") or model,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            finish_reason=choices[0].get("finish_reason"),
            latency_ms=latency,
        )

    def list_models(self) -> list[DiscoveredModel]:
        data = _get(f"{self.base_url}/models", headers=self._headers())
        items = data.get("data") or data.get("models") or []
        models: list[DiscoveredModel] = []
        for item in items:
            model_id = item.get("id") or item.get("name") or item.get("model")
            if not model_id:
                continue
            context = item.get("context_length") or item.get("context_window") or (item.get("meta") or {}).get("n_ctx_train")
            models.append(DiscoveredModel(model_id=model_id, context_window=int(context) if context else None))
        return models

    def embed(self, texts: list[str], model: str) -> list[list[float]]:
        data, _ = _post(
            f"{self.base_url}/embeddings", headers=self._headers(), body={"model": model, "input": texts}, timeout=60
        )
        return [item["embedding"] for item in sorted(data.get("data", []), key=lambda x: x.get("index", 0))]


class OpenAIAdapter(OpenAICompatibleAdapter):
    provider_type = "openai"
    default_base_url = "https://api.openai.com/v1"
    uses_max_completion_tokens = True


class OllamaAdapter(OpenAICompatibleAdapter):
    """Ollama through its native API.

    The native chat endpoint lets the client size the context window: Ollama
    serves models with a 4k-token context by default, which rejects long
    prompts (e.g. memory fact extraction, ~8k tokens) that the model itself
    could handle. The window grows in fixed steps so the model isn't reloaded
    for every request size.
    """

    provider_type = "ollama"
    default_base_url = "http://localhost:11434/v1"
    CONTEXT_STEPS = (4096, 8192, 16384, 32768)

    @property
    def _root(self) -> str:
        return self.base_url.removesuffix("/v1")

    def context_for(self, messages: list[ChatMessage], max_tokens: int) -> int:
        needed = int(sum(len(m.content) for m in messages) / 3.5) + max_tokens + 256
        return next((step for step in self.CONTEXT_STEPS if step >= needed), self.CONTEXT_STEPS[-1])

    def chat(self, messages: list[ChatMessage], model: str, params: ChatParams) -> ChatResult:
        body: dict[str, Any] = {
            "model": model,
            "stream": False,
            "messages": [
                {"role": m.role, "content": m.content, **({"images": [i.b64 for i in m.images]} if m.images else {})}
                for m in messages
            ],
            "options": {"temperature": params.temperature, "num_predict": params.max_tokens,
                        "num_ctx": self.context_for(messages, params.max_tokens)},
        }
        if params.json_schema is not None:
            body["format"] = params.json_schema
        elif params.json_mode:
            body["format"] = "json"
        data, latency = _post(f"{self._root}/api/chat", headers={"Content-Type": "application/json"}, body=body,
                              timeout=params.timeout_s)
        message = data.get("message") or {}
        return ChatResult(
            text=message.get("content") or "",
            model=data.get("model") or model,
            input_tokens=int(data.get("prompt_eval_count") or 0),
            output_tokens=int(data.get("eval_count") or 0),
            finish_reason=data.get("done_reason"),
            latency_ms=latency,
        )

    def list_models(self) -> list[DiscoveredModel]:
        try:
            data = _get(f"{self._root}/api/tags", headers={})
            return [DiscoveredModel(model_id=m["name"]) for m in data.get("models", []) if m.get("name")]
        except ProviderError:
            return super().list_models()


# ---------------------------------------------------------------------------
# Google Gemini (Generative Language API)
# ---------------------------------------------------------------------------


class GeminiAdapter(Adapter):
    provider_type = "gemini"
    default_base_url = "https://generativelanguage.googleapis.com/v1beta"
    supports_embeddings = True

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "x-goog-api-key": self.api_key or ""}

    def chat(self, messages: list[ChatMessage], model: str, params: ChatParams) -> ChatResult:
        system_parts = [m.content for m in messages if m.role == "system"]
        contents = []
        for message in messages:
            if message.role == "system":
                continue
            parts: list[dict[str, Any]] = [{"text": message.content}]
            for image in message.images:
                parts.append({"inline_data": {"mime_type": image.mime, "data": image.b64}})
            contents.append({"role": "model" if message.role == "assistant" else "user", "parts": parts})
        generation: dict[str, Any] = {"temperature": params.temperature, "maxOutputTokens": params.max_tokens}
        if params.json_mode or params.json_schema is not None:
            generation["responseMimeType"] = "application/json"
        body: dict[str, Any] = {"contents": contents, "generationConfig": generation}
        if system_parts:
            body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        model_path = model if model.startswith("models/") else f"models/{model}"
        data, latency = _post(
            f"{self.base_url}/{model_path}:generateContent", headers=self._headers(), body=body, timeout=params.timeout_s
        )
        candidates = data.get("candidates") or []
        if not candidates:
            feedback = data.get("promptFeedback") or {}
            raise ProviderError("bad_request", f"Gemini returned no candidates ({feedback.get('blockReason', 'unknown')})")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts if not part.get("thought"))
        usage = data.get("usageMetadata") or {}
        return ChatResult(
            text=text,
            model=model,
            input_tokens=int(usage.get("promptTokenCount") or 0),
            output_tokens=int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0),
            finish_reason=candidates[0].get("finishReason"),
            latency_ms=latency,
        )

    def list_models(self) -> list[DiscoveredModel]:
        models: list[DiscoveredModel] = []
        page_token = None
        for _ in range(5):
            url = f"{self.base_url}/models?pageSize=200" + (f"&pageToken={page_token}" if page_token else "")
            data = _get(url, headers=self._headers())
            for item in data.get("models", []):
                methods = item.get("supportedGenerationMethods") or []
                model_id = item.get("name", "").removeprefix("models/")
                if not model_id:
                    continue
                models.append(
                    DiscoveredModel(
                        model_id=model_id,
                        context_window=item.get("inputTokenLimit"),
                        capabilities={
                            "chat": "generateContent" in methods,
                            "embeddings": "embedContent" in methods,
                        },
                    )
                )
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return models

    def embed(self, texts: list[str], model: str) -> list[list[float]]:
        model_path = model if model.startswith("models/") else f"models/{model}"
        body = {"requests": [{"model": model_path, "content": {"parts": [{"text": t}]}} for t in texts]}
        data, _ = _post(f"{self.base_url}/{model_path}:batchEmbedContents", headers=self._headers(), body=body, timeout=60)
        return [item["values"] for item in data.get("embeddings", [])]


# ---------------------------------------------------------------------------
# Anthropic Claude (Messages API)
# ---------------------------------------------------------------------------


class AnthropicAdapter(Adapter):
    provider_type = "anthropic"
    default_base_url = "https://api.anthropic.com/v1"
    api_version = "2023-06-01"

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": self.api_key or "",
            "anthropic-version": self.api_version,
        }

    def chat(self, messages: list[ChatMessage], model: str, params: ChatParams) -> ChatResult:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        if params.json_mode or params.json_schema is not None:
            system += "\n\nRespond with a single valid JSON object and nothing else."
            if params.json_schema is not None:
                system += " It must match this JSON schema: " + json.dumps(params.json_schema)
        converted = []
        for message in messages:
            if message.role == "system":
                continue
            if message.images:
                content: Any = [
                    {"type": "image", "source": {"type": "base64", "media_type": img.mime, "data": img.b64}}
                    for img in message.images
                ] + [{"type": "text", "text": message.content}]
            else:
                content = message.content
            converted.append({"role": message.role, "content": content})
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": params.max_tokens,
            "messages": converted,
            "temperature": params.temperature,
        }
        if system.strip():
            body["system"] = system.strip()
        data, latency = _post(f"{self.base_url}/messages", headers=self._headers(), body=body, timeout=params.timeout_s)
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        usage = data.get("usage") or {}
        return ChatResult(
            text=text,
            model=data.get("model") or model,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            finish_reason=data.get("stop_reason"),
            latency_ms=latency,
        )

    def list_models(self) -> list[DiscoveredModel]:
        data = _get(f"{self.base_url}/models?limit=100", headers=self._headers())
        return [DiscoveredModel(model_id=item["id"]) for item in data.get("data", []) if item.get("id")]


ADAPTERS: dict[str, type[Adapter]] = {
    "openai": OpenAIAdapter,
    "openai_compatible": OpenAICompatibleAdapter,
    "ollama": OllamaAdapter,
    "gemini": GeminiAdapter,
    "anthropic": AnthropicAdapter,
}


def build_adapter(provider_type: str, *, api_key: str | None, base_url: str | None,
                  config: dict[str, Any] | None = None) -> Adapter:
    try:
        cls = ADAPTERS[provider_type]
    except KeyError as exc:
        raise ProviderError("unsupported", f"Unknown provider type {provider_type!r}") from exc
    return cls(api_key=api_key, base_url=base_url, config=config)
