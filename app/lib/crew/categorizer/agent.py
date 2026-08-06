"""CrewAI `Agent`/`Task` construction for the post-categorizer stage.

One agent, one task: given a post's actual content (title + primary keyword
+ mascot angle) and the brand's REAL WordPress category names, picks the
single best-fitting one -- or none, rather than guessing wrong. Same
`llm="deepseek/..."` wiring and manual-JSON-parsing posture as
`lib.crew.editor.agent` (see that module's docstring for why `output_pydantic`
isn't used with DeepSeek).

Exists because exact-string matching (`lib.crew.draft._resolve_category_id`'s
first pass) misses real, obvious fits whenever the idea's free-text category
label doesn't share literal words with the real category name -- e.g.
'Nutrition' should map to a real 'Food & Diet' category, but shares zero
words with it. This stage reads actual content and judges genuine topical
fit, the same way a human categorizing the post would.
"""

from __future__ import annotations

import json

from crewai import Agent, Task
from pydantic import BaseModel

from lib.crew.categorizer.models import CategoryChoice

DEEPSEEK_MODEL = "deepseek/deepseek-chat"

_CATEGORIZER_BACKSTORY = (
    "You are a careful site editor assigning ONE existing WordPress category to a new "
    "blog post. You only ever pick from the real categories you're given -- you never "
    "invent one, and you never force a fit that isn't genuinely there. When nothing in "
    "the list is a good match, you say so by returning an empty category_name rather "
    "than picking the closest-sounding wrong answer."
)


def _json_output_instructions(model: type[BaseModel]) -> str:
    """Same mechanism as `lib.crew.editor.agent._json_output_instructions`
    (kept as an independent copy, not a shared import -- matches this
    pipeline's established per-stage-independence posture)."""
    schema = json.dumps(model.model_json_schema())
    return (
        "Respond with ONLY a single valid JSON object -- no markdown code fences, "
        "no commentary before or after it, no trailing text. The JSON MUST validate "
        f"against this JSON Schema:\n{schema}"
    )


def build_categorizer_agent(*, model: str = DEEPSEEK_MODEL) -> Agent:
    """One `Agent`: role = post categorizer, no tools (works from the given text only)."""
    return Agent(
        role="Post Categorizer",
        goal=(
            "Pick the single best-fitting real WordPress category for one post, based "
            "on its actual content -- or none, if nothing genuinely fits."
        ),
        backstory=_CATEGORIZER_BACKSTORY,
        llm=model,
        tools=[],
        verbose=False,
        allow_delegation=False,
    )


def build_categorizer_task(agent: Agent, description: str) -> Task:
    """One `Task`. Structured output is enforced by manual JSON parsing in
    `lib.crew.categorizer.execute.execute_categorizer_crew` -- NOT
    `output_pydantic` (see module docstring)."""
    return Task(
        description=description,
        expected_output=(
            "A JSON object matching the CategoryChoice schema: category_name.\n\n"
            f"{_json_output_instructions(CategoryChoice)}"
        ),
        agent=agent,
    )


def build_categorizer_task_description(
    *, title: str, extra_context: str, real_category_names: list[str]
) -> str:
    """The categorizer agent's full prompt: one post's content -> a `CategoryChoice`.

    `extra_context` is deliberately generic (not e.g. `primary_keyword`/
    `mascot_angle` specifically) -- callers may only have a title and the
    originating idea's free-text category label (`lib.crew.draft`, which
    never touches `ContentBrief`), not the writer pipeline's richer fields.

    Short enough to inline here rather than a separate `prompts.py` (unlike
    `lib.crew.editor.prompts`'s long rubric)."""
    categories_list = "\n".join(f'- "{name}"' for name in real_category_names)
    context_line = f"\n{extra_context}" if extra_context.strip() else ""
    return f"""You are assigning ONE existing WordPress category to a new blog post.

## Real categories on this site (choose ONE of these exact strings, or none)
{categories_list}

## Post content
Title: {title}{context_line}

## Your job
Pick the single category above that this post's content genuinely, topically belongs \
in -- copy its name EXACTLY as listed (character-for-character). If none of the \
categories are a genuine fit, return an empty string for category_name. Do not force a \
loose or partial match, and never invent a category name that isn't in the list above.
"""
