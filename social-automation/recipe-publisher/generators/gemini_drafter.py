"""Gemini-backed voice drafter — wraps recipe_from_seed_gemini.generate_from_seed_gemini.

Stateless wrapper. The underlying function reads `GEMINI_API_KEY` and
`GEMINI_VOICE_MODEL` from the environment on each call, which matches the
existing behavior — we deliberately don't snapshot at construction time so
that env-var changes between calls (e.g. test fixtures) take effect.
"""

from __future__ import annotations

from typing import Any

from .recipe_from_seed_gemini import generate_from_seed_gemini
from .seeds import RecipeSeed


class GeminiDrafter:
    """`Drafter` implementation backed by Gemini's generateContent endpoint.

    Stateless — env vars read by the underlying call on each invocation.
    """

    def draft_voice(
        self,
        topic: str,
        seed: RecipeSeed,
        *,
        extra_instructions: str | None = None,
    ) -> dict[str, Any]:
        return generate_from_seed_gemini(topic, seed, extra_instructions=extra_instructions)
