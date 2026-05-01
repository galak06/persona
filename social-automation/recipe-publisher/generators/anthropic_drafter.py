"""Anthropic-backed voice drafter — wraps recipe_from_seed.generate_from_seed.

Constructs an Anthropic client at instantiation (or accepts an injected one
for tests). Reads the model from `RECIPE_MODEL` env var with a sensible
default. Delegates the actual tool-call mechanics to the existing
`recipe_from_seed.generate_from_seed`.
"""

from __future__ import annotations

import os
from typing import Any

from .recipe_from_seed import generate_from_seed
from .seeds import RecipeSeed

_DEFAULT_MODEL = "claude-sonnet-4-6"


class AnthropicDrafter:
    """`Drafter` implementation backed by the Anthropic SDK.

    Args:
        client: Optional pre-built `anthropic.Anthropic` client. None
            constructs a fresh one (honors `ANTHROPIC_API_KEY` env). Tests
            inject a mock here.
        model: Model name. None reads `RECIPE_MODEL` env var, falling back
            to `claude-sonnet-4-6`.
    """

    def __init__(self, *, client: object | None = None, model: str | None = None) -> None:
        if client is None:
            from anthropic import Anthropic

            client = Anthropic()
        self._client: object = client
        self._model: str = model or os.environ.get("RECIPE_MODEL") or _DEFAULT_MODEL

    def draft_voice(self, topic: str, seed: RecipeSeed) -> dict[str, Any]:
        """Delegate to the existing `generate_from_seed` for the actual tool call."""
        return generate_from_seed(topic, seed, client=self._client, model=self._model)  # type: ignore[arg-type]
