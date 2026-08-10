"""CrewAI `Agent`/`Task` construction for the fallback Reel Beats stage.

Only reached when the primary OpenArt path fails (see `lib.crew.reels`'s
package docstring and `scripts/crewai_reels_pipeline.py`). `DEEPSEEK_MODEL`
is redefined locally rather than imported from a sibling package -- matches
this repo's "independently-evolving pipelines" convention (`lib.crew.idea`,
`lib.crew.trends` do the same).

Deliberately does NOT use `Task(output_pydantic=...)` -- same DeepSeek/
CrewAI `response_format` incompatibility documented in
`lib.crew.idea.agent`'s module docstring. Ask for raw JSON via the prompt
(`_json_output_instructions`) and parse it manually in
`lib.crew.reels.execute`.
"""

from __future__ import annotations

import json

from crewai import Agent, Task
from pydantic import BaseModel

from lib.crew.reels.models import ReelPlan

DEEPSEEK_MODEL = "deepseek/deepseek-chat"

_AGENT_BACKSTORY = (
    "You are a short-form video creative director for a niche blog's Instagram "
    "and Facebook Reels. You write tight, on-screen scripts that hook a viewer "
    "in the first seconds and drive them to read the full post -- never a slow "
    "build, never filler. You know this brand's voice and never invent claims "
    "the source post doesn't actually make."
)


def _json_output_instructions(model: type[BaseModel]) -> str:
    """Explicit "respond with ONLY this JSON schema" instructions, embedding
    the model's real `model_json_schema()` -- same mechanism
    `lib.crew.idea.agent._json_output_instructions` uses."""
    schema = json.dumps(model.model_json_schema())
    return (
        "Respond with ONLY a single valid JSON object -- no markdown code fences, "
        "no commentary before or after it, no trailing text. The JSON MUST validate "
        f"against this JSON Schema:\n{schema}"
    )


def build_reels_agent(*, model: str = DEEPSEEK_MODEL) -> Agent:
    """One `Agent`: role = short-form video script writer, no tools (works
    only from the post content it's given)."""
    return Agent(
        role="Reel Beats Writer",
        goal=(
            "Turn a published blog post into a fixed 5-beat, on-screen Reel script "
            "(hook, three points, one CTA) plus Instagram and Facebook captions, "
            "written to genuinely hook attention and drive traffic to the site."
        ),
        backstory=_AGENT_BACKSTORY,
        llm=model,
        tools=[],
        verbose=False,
        allow_delegation=False,
    )


def build_reels_task(agent: Agent, description: str) -> Task:
    """One `Task`. Structured output is enforced by manual JSON parsing in
    `lib.crew.reels.execute.execute_reels_crew` (see module docstring)."""
    return Task(
        description=description,
        expected_output=(
            "A JSON object matching the ReelPlan schema: exactly 5 beats (headline + "
            "subcopy + image_prompt each), an ig_caption, and an fb_caption."
            f"\n\n{_json_output_instructions(ReelPlan)}"
        ),
        agent=agent,
    )
