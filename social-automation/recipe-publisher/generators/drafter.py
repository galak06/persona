"""Voice-drafter Protocol — provider-agnostic interface for recipe voice generation.

A `Drafter` produces the voice fields (title, intro, FAQ, captions, image
brief, etc.) for a seed-grounded recipe. Two implementations live alongside:

    - `AnthropicDrafter` (anthropic_drafter.py) — Anthropic SDK + tool calling
    - `GeminiDrafter` (gemini_drafter.py) — direct HTTP to Gemini's
      generateContent endpoint with function-calling

Provider selection happens at the factory boundary (`get_drafter`), driven by
the `VOICE_PROVIDER` env var with auto-detection from which API key is set.
Callers (recipe.py) work against the Protocol — they don't import
provider-specific modules.

Output contract:
    Both implementations return `dict[str, Any]` with at least these keys:
        title, meta_description, intro, verdict, image_brief, ig_caption,
        faq (list of {question, answer})
    Provider-specific extra fields are allowed but not required by callers.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

from .seeds import RecipeSeed


class Drafter(Protocol):
    """Generate voice fields for a seed-grounded recipe.

    Implementations encapsulate provider-specific concerns: API client setup,
    model selection, tool-schema translation, response parsing. The shared
    output shape lets callers treat providers as interchangeable.
    """

    def draft_voice(self, topic: str, seed: RecipeSeed) -> dict[str, Any]:
        """Produce the voice fields for `topic` grounded in `seed`.

        Args:
            topic: The recipe topic (free-form text matched against seeds).
            seed: The vetted seed providing frozen ingredients/steps/times.

        Returns:
            Voice fields dict with `title`, `meta_description`, `intro`,
            `verdict`, `image_brief`, `ig_caption`, `faq[]`.

        Raises:
            RuntimeError: On provider-side failures (API errors, malformed
                tool calls). Always include enough context to debug —
                model name, topic, seed id.
        """
        ...


def get_drafter(provider: str | None = None) -> Drafter:
    """Factory: return the configured Drafter for the active provider.

    Args:
        provider: Override the env-driven selection. One of `"anthropic"` |
            `"gemini"`. None reads `VOICE_PROVIDER` env var, falling back
            to auto-detection (Gemini if its key is set and Anthropic is
            not, else Anthropic).

    Returns:
        A `Drafter` instance ready for `.draft_voice(topic, seed)`.

    Raises:
        ValueError: On unknown provider name.
    """
    name = (provider or os.environ.get("VOICE_PROVIDER") or _auto_detect_provider()).lower()
    if name == "anthropic":
        # Deferred imports keep the module loadable without optional deps installed.
        from .anthropic_drafter import AnthropicDrafter

        return AnthropicDrafter()
    if name == "gemini":
        from .gemini_drafter import GeminiDrafter

        return GeminiDrafter()
    raise ValueError(f"unknown VOICE_PROVIDER={name!r}; expected 'anthropic' or 'gemini'")


def _auto_detect_provider() -> str:
    """Default provider: Gemini if its key is set and Anthropic is not."""
    if os.environ.get("GEMINI_API_KEY") and not os.environ.get("ANTHROPIC_API_KEY"):
        return "gemini"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "gemini"
