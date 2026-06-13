"""Generic OpenAI wrapper — deliberately email-agnostic so every feature reuses it.

Exposes `classify` and `generate`, each returning the text alongside token usage
and an estimated cost. Any provider error is raised as `LLMError` so callers can
degrade gracefully (the email pipeline turns it into ``{"action": "ignore"}``).
"""

from __future__ import annotations

from dataclasses import dataclass

from openai import OpenAI, OpenAIError

from app.config import settings


class LLMError(Exception):
    """Raised on any LLM provider failure. Callers catch and degrade."""


# Rough USD per 1K tokens, used only for dashboard cost estimates. Tweak freely.
_COST_PER_1K = {
    "gpt-4o-mini": 0.00015,
    "gpt-4o": 0.005,
}


@dataclass
class LLMResult:
    text: str
    tokens: int
    cost: float
    model: str


_client: OpenAI | None = None


def _get_client() -> OpenAI:
    """Lazily build the OpenAI client (no network at import time)."""
    global _client
    if _client is None:
        if not settings.OPENAI_API_KEY:
            raise LLMError("OPENAI_API_KEY is not configured")
        _client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


def _estimate_cost(model: str, tokens: int) -> float:
    rate = _COST_PER_1K.get(model, 0.0005)
    return round((tokens / 1000) * rate, 6)


def _chat(system_prompt: str, user_content: str, model: str) -> LLMResult:
    try:
        resp = _get_client().chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.4,
        )
    except OpenAIError as exc:  # network, auth, rate-limit, etc.
        raise LLMError(str(exc)) from exc

    text = (resp.choices[0].message.content or "").strip()
    tokens = resp.usage.total_tokens if resp.usage else 0
    return LLMResult(text=text, tokens=tokens, cost=_estimate_cost(model, tokens), model=model)


def classify(system_prompt: str, user_content: str, model: str | None = None) -> LLMResult:
    """Cheap-model triage. Returns an `LLMResult` whose `text` is the label."""
    return _chat(system_prompt, user_content, model or settings.OPENAI_CLASSIFY_MODEL)


def generate(system_prompt: str, user_content: str, model: str | None = None) -> LLMResult:
    """Drafting / generation. Returns an `LLMResult` with the produced text."""
    return _chat(system_prompt, user_content, model or settings.OPENAI_DRAFT_MODEL)
