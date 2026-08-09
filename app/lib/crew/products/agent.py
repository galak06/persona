"""CrewAI `Agent`/`Task` construction for the affiliate product-selector stage.

One agent, one task: given a post's plan (title + keywords + section outline)
and the brand's REAL affiliate product catalog candidates, picks at most N
products a reader of that post would genuinely want -- or none, rather than
forcing irrelevant products in. Same `llm="deepseek/..."` wiring and
manual-JSON-parsing posture as `lib.crew.categorizer.agent` (see
`lib.crew.writer.agent`'s module docstring for why `output_pydantic`
isn't used with DeepSeek).

Exists so product placement is judged from the post's actual plan rather
than left to the writer agent's drafting pass: a dedicated curator reading
the real catalog picks genuine fits (or declines), the same way a human
editor choosing affiliate placements for the post would.
"""

from __future__ import annotations

import json

from crewai import Agent, Task
from pydantic import BaseModel

from lib.crew.products.models import ProductSelection

DEEPSEEK_MODEL = "deepseek/deepseek-chat"

_SELECTOR_BACKSTORY = (
    "You are a careful affiliate product curator for a dog-lifestyle blog. You only "
    "ever pick from the real product catalog you're given -- you never invent a "
    "product key, and you never force a product into a post it doesn't genuinely "
    "serve. When nothing in the catalog fits the post, you say so by returning an "
    "empty products list rather than padding the post with irrelevant picks."
)


def _json_output_instructions(model: type[BaseModel]) -> str:
    """Same mechanism as `lib.crew.categorizer.agent._json_output_instructions`
    (kept as an independent copy, not a shared import -- matches this
    pipeline's established per-stage-independence posture)."""
    schema = json.dumps(model.model_json_schema())
    return (
        "Respond with ONLY a single valid JSON object -- no markdown code fences, "
        "no commentary before or after it, no trailing text. The JSON MUST validate "
        f"against this JSON Schema:\n{schema}"
    )


def build_product_selector_agent(*, model: str = DEEPSEEK_MODEL) -> Agent:
    """One `Agent`: role = product curator, no tools (works from the given text only)."""
    return Agent(
        role="Affiliate Product Curator",
        goal=(
            "Pick the few real affiliate products a reader of one specific post "
            "would genuinely want, based on the post's actual plan -- or none, "
            "if nothing in the catalog genuinely fits."
        ),
        backstory=_SELECTOR_BACKSTORY,
        llm=model,
        tools=[],
        verbose=False,
        allow_delegation=False,
    )


def build_product_selector_task(agent: Agent, description: str) -> Task:
    """One `Task`. Structured output is enforced by manual JSON parsing in
    `lib.crew.products.execute.execute_product_selector_crew` -- NOT
    `output_pydantic` (see module docstring)."""
    return Task(
        description=description,
        expected_output=(
            "A JSON object matching the ProductSelection schema: products -- a list "
            "of key/reason pairs using only real catalog keys, or an empty list when "
            f"nothing genuinely fits.\n\n{_json_output_instructions(ProductSelection)}"
        ),
        agent=agent,
    )
