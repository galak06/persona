"""CrewAI `Agent`/`Task` construction for the content-idea synthesis stage.

One agent, one task. This agent is the second hop of the split scout
pipeline (`lib.crew.trends` surfaces raw signals; this stage turns them into
concrete, brand-voiced, publishable post ideas) -- see `lib.crew.scout` for
the orchestration that wires the two together. `DEEPSEEK_MODEL` is redefined
locally rather than imported from `lib.crew.trends.agent` -- these are two
independently-evolving pipelines (surfacing vs. synthesis); a shared constant
would silently couple their model choice.

This agent gets no tools (`tools=[]`): unlike the old combined scout (and
unlike `lib.crew.trends`'s agent), it never searches the web itself. It works
purely from the trend signals `lib.crew.trends` already gathered, plus the
brand's own voice and seed-keyword context -- pure synthesis, no discovery.

Deliberately does NOT use `Task(output_pydantic=...)`: live-tested against
DeepSeek this build and confirmed (with a minimal, isolated repro using the
old scout's exact `output_pydantic` pattern) that `crewai==1.15.12`'s
structured-output path sends OpenAI's strict `json_schema` `response_format`
even for `deepseek/...` models, which DeepSeek's API currently rejects
outright ("This response_format type is unavailable now" -- a 400 on
literally every `output_pydantic` call, while a plain-text call on the same
model/agent succeeds). This is an upstream crewai/DeepSeek incompatibility,
not specific to this pipeline's prompts -- it would break this agent
identically if left on `output_pydantic`. Route around it instead: ask for
raw JSON via the prompt (`_json_output_instructions`, embedding the real
Pydantic schema) and parse it manually in `lib.crew.idea.execute`.
"""

from __future__ import annotations

import json

from crewai import Agent, Task
from pydantic import BaseModel

from lib.crew.models import ScoutOutput

DEEPSEEK_MODEL = "deepseek/deepseek-chat"

_AGENT_BACKSTORY = (
    "You are a senior content strategist for a niche blog. You are grounded, "
    "specific, and never propose generic filler topics -- every idea you "
    "surface is synthesized from real trend signals someone else already "
    "found, never invented from thin air. You know this brand's voice and "
    "audience and only propose ideas that genuinely fit."
)


def _json_output_instructions(model: type[BaseModel]) -> str:
    """Explicit "respond with ONLY this JSON schema" instructions, embedding
    the model's real `model_json_schema()` -- more precise than a prose
    description, and the mechanism `lib.crew.idea.execute` relies on to
    manually parse+validate the raw response (see module docstring)."""
    schema = json.dumps(model.model_json_schema())
    return (
        "Respond with ONLY a single valid JSON object -- no markdown code fences, "
        "no commentary before or after it, no trailing text. The JSON MUST validate "
        f"against this JSON Schema:\n{schema}"
    )


def build_idea_agent(*, model: str = DEEPSEEK_MODEL) -> Agent:
    """One `Agent`: role = content idea synthesizer, no tools (works only from
    the trend signals it's given, never searches live)."""
    return Agent(
        role="Content Idea Synthesizer",
        goal=(
            "Turn already-gathered trend signals into a single, deduped, ranked "
            "list of concrete, publishable content ideas grounded in this brand's "
            "real identity, voice, and audience."
        ),
        backstory=_AGENT_BACKSTORY,
        llm=model,
        tools=[],
        verbose=False,
        allow_delegation=False,
    )


def build_idea_task(agent: Agent, description: str) -> Task:
    """One `Task`. Structured output is enforced by manual JSON parsing in
    `lib.crew.idea.execute.execute_idea_crew` (see module docstring for why --
    NOT `output_pydantic`)."""
    return Task(
        description=description,
        expected_output=(
            "A JSON object matching the ScoutOutput schema: a list of ranked, "
            "deduped content ideas, each with topic, target_keyword, category, "
            f"reasoning, opportunity_type, and priority_score.\n\n{_json_output_instructions(ScoutOutput)}"
        ),
        agent=agent,
    )
