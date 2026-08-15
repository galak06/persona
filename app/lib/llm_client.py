"""Provider-agnostic text-LLM client — the one place a vendor gets chosen.

A ``TextLLM`` produces plain text (``complete``) or a JSON object against a
caller-supplied schema (``complete_json``). Two implementations live here:

    - ``GeminiLLM`` — adapts ``lib.gemini_client`` (HTTP ``generateContent``,
      key handling, ``thinkingBudget``, Langfuse tracing). Every Gemini-shaped
      concern stays behind that module; none of it leaks into ``LLMRequest``.
    - ``AnthropicLLM`` — the Anthropic Messages API (``system=`` is a
      first-class param there; JSON rides the prompt, since there is no
      ``responseSchema`` knob).

Provider selection happens at the factory boundary (``get_llm`` /
``resolve_provider``), driven by the ``VOICE_PROVIDER`` env var with
auto-detection from which API key is set — the exact rule
``recipe-publisher/generators/drafter.py`` already uses for the recipe-voice
Protocol, so the engine has ONE selection dialect, not three. Callers
(``lib.draft_helper``, ``lib.reply_drafter``,
``recipe-publisher/workers/_llm.py``) work against the Protocol / the rule
and never import a provider module themselves.

Failure contract: BOTH methods return ``None`` on ANY failure — missing key,
non-2xx, no candidates, malformed JSON, SDK exception. Callers already treat
``None`` (and ``engage is not True``) as "no draft, skip this item", so a
provider swap can never turn a failure into a crash.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, replace
from types import ModuleType
from typing import Any, Final, Protocol

logger = logging.getLogger(__name__)

_PROVIDERS: Final[tuple[str, ...]] = ("anthropic", "gemini")
_ANTHROPIC_DEFAULT_MODEL: Final[str] = "claude-sonnet-4-6"
_JSON_OBJECT_RE: Final[re.Pattern[str]] = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class LLMRequest:
    """One completion request, in terms every vendor supports.

    ``trace_name`` names the Langfuse generation for the Gemini path; keep it
    stable when moving a caller onto this client so dashboards stay
    continuous.
    """

    user: str
    system: str | None = None
    max_tokens: int = 400
    temperature: float = 0.7
    trace_name: str = "llm-call"


class TextLLM(Protocol):
    """Text generation, provider-agnostic. ``None`` means "call failed"."""

    def complete(self, req: LLMRequest) -> str | None:
        """Plain text for ``req``, or ``None`` on any failure."""
        ...

    def complete_json(
        self, req: LLMRequest, *, response_schema: dict[str, Any]
    ) -> dict[str, Any] | None:
        """A JSON object shaped like ``response_schema`` (an OpenAPI-3 subset:
        ``type``/``properties``/``required``, no ``$ref``), or ``None`` on any
        failure. Callers own validating the parsed shape."""
        ...


def resolve_provider(provider: str | None = None) -> str:
    """Return the active provider name: explicit arg → ``VOICE_PROVIDER`` env
    → auto-detection from which API key is set.

    Shared with ``recipe-publisher/workers/_llm.py``, which keeps its own
    transports (different model/temperature budgets) but must not invent its
    own selection rule.

    Raises:
        ValueError: On an unknown provider name.
    """
    name = (provider or os.environ.get("VOICE_PROVIDER") or _auto_detect_provider()).lower()
    if name not in _PROVIDERS:
        raise ValueError(f"unknown VOICE_PROVIDER={name!r}; expected 'anthropic' or 'gemini'")
    return name


def _auto_detect_provider() -> str:
    """Default provider: Gemini if its key is set and Anthropic is not."""
    if os.environ.get("GEMINI_API_KEY") and not os.environ.get("ANTHROPIC_API_KEY"):
        return "gemini"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "gemini"


def get_llm(provider: str | None = None) -> TextLLM:
    """Factory: the configured ``TextLLM`` for the active provider.

    Args:
        provider: Override the env-driven selection (``"anthropic"`` |
            ``"gemini"``). None follows ``resolve_provider``.

    Raises:
        ValueError: On an unknown provider name.
    """
    if resolve_provider(provider) == "anthropic":
        return AnthropicLLM()
    return GeminiLLM()


def _gemini_transport() -> ModuleType:
    """Import the Gemini transport lazily.

    Deferred so this module stays importable — and free of ``httpx`` — for
    callers that only want ``resolve_provider``.

    Until 2026-08-15 this carried a try/except fallback to a bare
    ``import gemini_client``, tolerating the second sys.path layout that
    ``scripts/`` bootstrapped. There is one layout now — ``app`` on the path,
    everything under the ``lib.`` namespace — so the fallback is gone.
    """
    from lib import gemini_client

    return gemini_client


class GeminiLLM:
    """``TextLLM`` backed by ``lib.gemini_client``.

    A thin adapter on purpose: the endpoint, API-key handling, payload shape
    (``thinkingConfig``, ``responseSchema``) and Langfuse tracing all remain
    that module's business, so swapping providers here can't disturb them.
    """

    def complete(self, req: LLMRequest) -> str | None:
        gemini = _gemini_transport()
        text: str | None = gemini._call_gemini(
            req.user,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
            system=req.system,
            trace_name=req.trace_name,
        )
        return text

    def complete_json(
        self, req: LLMRequest, *, response_schema: dict[str, Any]
    ) -> dict[str, Any] | None:
        gemini = _gemini_transport()
        parsed: dict[str, Any] | None = gemini.call_json(
            req.user,
            schema=response_schema,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
            trace_name=req.trace_name,
            system=req.system,
        )
        return parsed


class AnthropicLLM:
    """``TextLLM`` backed by the Anthropic Messages API.

    Args:
        client: Optional pre-built ``anthropic.Anthropic``. None constructs one
            (honours ``ANTHROPIC_API_KEY``); tests inject a fake here.
        model: Model name. None reads ``ANTHROPIC_MODEL``, falling back to
            ``claude-sonnet-4-6``.
    """

    def __init__(self, *, client: Any = None, model: str | None = None) -> None:
        if client is None:
            # Deferred: the SDK is optional for Gemini-only deployments.
            from anthropic import Anthropic

            client = Anthropic()
        self._client: Any = client
        self._model: str = model or os.environ.get("ANTHROPIC_MODEL") or _ANTHROPIC_DEFAULT_MODEL

    def complete(self, req: LLMRequest) -> str | None:
        """Plain text, or ``None`` on any SDK/API failure (never raises) —
        the same degrade-don't-crash contract as the Gemini path."""
        kwargs: dict[str, Any] = {"system": req.system} if req.system else {}
        try:
            msg = self._client.messages.create(
                model=self._model,
                max_tokens=req.max_tokens,
                temperature=req.temperature,
                messages=[{"role": "user", "content": req.user}],
                **kwargs,
            )
            text = "".join(getattr(block, "text", "") for block in msg.content).strip()
        except Exception as e:  # any SDK error degrades to a skip, never a crash
            logger.warning("anthropic call failed: %s", e)
            return None
        return text or None

    def complete_json(
        self, req: LLMRequest, *, response_schema: dict[str, Any]
    ) -> dict[str, Any] | None:
        """The Messages API has no ``responseSchema``, so the schema rides the
        user prompt and the reply is parsed defensively (fences tolerated)."""
        instructed = replace(req, user=f"{req.user}\n\n{_json_instruction(response_schema)}")
        text = self.complete(instructed)
        return _extract_json_object(text) if text else None


def _json_instruction(schema: dict[str, Any]) -> str:
    return (
        "Respond with ONLY a JSON object matching this schema — no markdown "
        f"fences, no preamble, no text after it:\n{json.dumps(schema)}"
    )


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """First JSON object in ``text`` (markdown fences stripped), or ``None``
    when there is none / it doesn't parse / it isn't an object."""
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", text, flags=re.IGNORECASE)
    match = _JSON_OBJECT_RE.search(cleaned)
    if not match:
        logger.warning("llm json response has no JSON object: %s", text[:200])
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        logger.warning("llm json response is not valid JSON: %s", text[:200])
        return None
    if not isinstance(parsed, dict):
        logger.warning("llm json response is not an object: %s", text[:200])
        return None
    return parsed
