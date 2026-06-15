"""Generic Anthropic wrapper — deliberately email-agnostic so every feature reuses it.

Exposes `classify` and `generate`, each returning the text alongside token usage
and an estimated cost. Any provider error is raised as `LLMError` so callers can
degrade gracefully (the email pipeline turns it into ``{"action": "ignore"}``).
"""

from __future__ import annotations

from dataclasses import dataclass

from anthropic import Anthropic, APIError

from app.config import settings


class LLMError(Exception):
    """Raised on any LLM provider failure. Callers catch and degrade."""


# Claude Haiku 4.5 pricing: $1.00/1M input, $5.00/1M output
_INPUT_COST_PER_1K = 0.001
_OUTPUT_COST_PER_1K = 0.005


@dataclass
class LLMResult:
    text: str
    tokens: int
    cost: float
    model: str


_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        if not settings.ANTHROPIC_API_KEY:
            raise LLMError("ANTHROPIC_API_KEY is not configured")
        _client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


def _estimate_cost(input_tokens: int, output_tokens: int) -> float:
    return round(
        (input_tokens / 1000) * _INPUT_COST_PER_1K
        + (output_tokens / 1000) * _OUTPUT_COST_PER_1K,
        6,
    )


def _chat(system_prompt: str, user_content: str, *, max_tokens: int) -> LLMResult:
    model = settings.ANTHROPIC_MODEL
    try:
        resp = _get_client().messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
    except APIError as exc:
        raise LLMError(str(exc)) from exc

    text = (resp.content[0].text if resp.content else "").strip()
    input_tokens = resp.usage.input_tokens
    output_tokens = resp.usage.output_tokens
    total_tokens = input_tokens + output_tokens
    return LLMResult(
        text=text,
        tokens=total_tokens,
        cost=_estimate_cost(input_tokens, output_tokens),
        model=model,
    )


def classify(system_prompt: str, user_content: str, model: str | None = None) -> LLMResult:
    """Cheap-model triage. Returns an `LLMResult` whose `text` is the label."""
    # model param kept for API compatibility but ignored — always uses ANTHROPIC_MODEL
    return _chat(system_prompt, user_content, max_tokens=512)


def generate(system_prompt: str, user_content: str, model: str | None = None) -> LLMResult:
    """Drafting / generation. Returns an `LLMResult` with the produced text."""
    return _chat(system_prompt, user_content, max_tokens=4096)
