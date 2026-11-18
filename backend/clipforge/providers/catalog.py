"""Model capability inference and reference pricing.

Discovery (``/models``) returns ids but rarely capabilities or prices, so the
router infers them from model-family naming conventions. Every inferred value
can be overridden per model in *AI Connections → Advanced*.

Prices are USD per million tokens. ``price_source`` records where a number
came from; reference prices are published list prices as of the stated date
and must be verified by the owner — the UI marks them as estimates. Unknown
models get no price: the budget guard then uses a conservative estimate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Conservative estimate (USD / 1M tokens) used for budget checks when a paid
#: model's price is unknown. Deliberately on the expensive side.
UNKNOWN_PRICE_IN = 3.0
UNKNOWN_PRICE_OUT = 15.0

REFERENCE_PRICES_AS_OF = "2025-11"

# (regex on model id, price in, price out)
_REFERENCE_PRICES: list[tuple[str, float, float]] = [
    (r"^gpt-4o-mini", 0.15, 0.60),
    (r"^gpt-4o(?!-mini)", 2.50, 10.00),
    (r"^gpt-4\.1-nano", 0.10, 0.40),
    (r"^gpt-4\.1-mini", 0.40, 1.60),
    (r"^gpt-4\.1(?!-)", 2.00, 8.00),
    (r"^gpt-5-nano", 0.05, 0.40),
    (r"^gpt-5-mini", 0.25, 2.00),
    (r"^gpt-5(?![-.]\d)(?!-mini|-nano)", 1.25, 10.00),
    (r"^text-embedding-3-small", 0.02, 0.0),
    (r"^text-embedding-3-large", 0.13, 0.0),
    (r"^gemini-2\.5-flash-lite", 0.10, 0.40),
    (r"^gemini-2\.5-flash(?!-lite)", 0.30, 2.50),
    (r"^gemini-2\.5-pro", 1.25, 10.00),
    (r"^gemini-2\.0-flash", 0.10, 0.40),
    (r"^claude-3-5-haiku", 0.80, 4.00),
    (r"^claude-haiku-4-5", 1.00, 5.00),
    (r"^claude-sonnet-4", 3.00, 15.00),
    (r"^claude-opus-4-5", 5.00, 25.00),
    (r"^claude-opus-4(-0|-1)", 15.00, 75.00),
]

_FAST = re.compile(r"(nano|mini|haiku|flash|lite|small|tiny|0\.5b|1\.5b|\b1b\b|\b3b\b|smollm|phi|gemma-?[23]?-?[12]b|360m|135m)", re.IGNORECASE)
_BEST = re.compile(r"(opus|gpt-5(?!.*(mini|nano))|\bo3\b(?!-mini)|gemini-[\d.]+-pro|sonnet|gpt-4\.1(?!-(mini|nano))|gpt-4o(?!-mini)|70b|72b|405b|deepseek-(v3|r1|reasoner)|mistral-large|qwen.*(32b|72b))", re.IGNORECASE)
_VISION = re.compile(r"(gpt-4o|gpt-4\.1|gpt-5|gemini|claude-(3|haiku|sonnet|opus)|llava|vision|-vl|gemma-?3|pixtral|llama-?3\.2-(11|90)b|qwen.*vl)", re.IGNORECASE)
_EMBED = re.compile(r"(embed|embedding|bge|e5-|nomic|minilm)", re.IGNORECASE)
_NON_CHAT = re.compile(r"(whisper|tts|dall-e|image|imagen|veo|moderation|audio|realtime|transcribe|search|computer-use|aqa)", re.IGNORECASE)


@dataclass
class ModelProfile:
    quality_tier: int
    vision: bool
    embeddings: bool
    chat: bool
    json: bool
    context_window: int
    price_in: float | None
    price_out: float | None
    price_source: str


def default_context(provider_type: str, model_id: str) -> int:
    m = model_id.lower()
    if provider_type == "gemini" or "gemini" in m:
        return 1_000_000
    if provider_type == "anthropic" or m.startswith("claude"):
        return 200_000
    if m.startswith(("gpt-4.1", "gpt-5")):
        return 400_000 if m.startswith("gpt-5") else 1_000_000
    if m.startswith(("gpt-4o", "o1", "o3", "o4")):
        return 128_000
    return 8_192


def reference_price(model_id: str) -> tuple[float | None, float | None, str]:
    for pattern, price_in, price_out in _REFERENCE_PRICES:
        if re.search(pattern, model_id.lower()):
            return price_in, price_out, f"reference:{REFERENCE_PRICES_AS_OF}"
    return None, None, "unknown"


def profile(provider_type: str, model_id: str, *, is_local: bool, context_window: int | None = None) -> ModelProfile:
    m = model_id.lower()
    tier = 3 if _BEST.search(m) else 1 if _FAST.search(m) else 2
    if is_local:
        tier = min(tier, 2)
        # Small local models are useful for classification, not judgement.
        if re.search(r"(0\.5b|360m|135m|\b1b\b|1\.5b)", m):
            tier = 1
    embeddings = bool(_EMBED.search(m))
    chat = not embeddings and not _NON_CHAT.search(m)
    if is_local:
        price_in, price_out, source = 0.0, 0.0, "local"
    else:
        price_in, price_out, source = reference_price(m)
    return ModelProfile(
        quality_tier=tier,
        vision=bool(_VISION.search(m)) and chat,
        embeddings=embeddings,
        chat=chat,
        json=chat,
        context_window=context_window or default_context(provider_type, m),
        price_in=price_in,
        price_out=price_out,
        price_source=source,
    )


#: Sensible defaults offered when a provider is added, so the owner does not
#: have to know model ids. Discovery replaces/extends these when it succeeds.
SUGGESTED_MODELS: dict[str, list[str]] = {
    "openai": ["gpt-5-mini", "gpt-5", "gpt-4.1-mini"],
    "gemini": ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro"],
    "anthropic": ["claude-haiku-4-5", "claude-sonnet-4-5"],
    "ollama": [],
    "openai_compatible": [],
}

PROVIDER_LABELS = {
    "openai": "OpenAI",
    "gemini": "Google Gemini",
    "anthropic": "Anthropic Claude",
    "ollama": "Ollama (local)",
    "openai_compatible": "OpenAI-compatible endpoint",
}


def estimate_tokens(text: str) -> int:
    """Rough token estimate (≈4 characters per token for English)."""
    return max(1, len(text) // 4)
