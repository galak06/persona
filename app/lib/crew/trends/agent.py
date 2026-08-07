"""CrewAI `Agent`/`Task` construction for the market-trend scout.

One agent, one task. Same `llm="deepseek/..."` string wiring as
`lib.crew.agent`'s (current) scout -- `DEEPSEEK_MODEL` is redefined locally
rather than imported, matching `lib.crew.writer.agent`'s "independently-
evolving pipelines" convention: a shared constant would silently couple this
stage's model choice to the Idea stage's (or any other pipeline's).

This agent inherits the search/GSC-carrying-forward half of the old combined
scout's responsibility -- surfacing trend signals from GSC data, live web
search, and (when available) the Instagram trends feed -- but explicitly NOT
the synthesis-into-publishable-ideas half; that belongs to a separate Idea
agent downstream (`lib.crew.idea`) which reads this agent's `TrendsOutput`.

Deliberately does NOT use `Task(output_pydantic=...)`: live-tested against
DeepSeek this build and confirmed (with a minimal, isolated repro using the
old scout's exact `output_pydantic` pattern -- see `lib.crew.writer.agent`'s
module docstring for the original repro) that `crewai==1.15.12`'s
structured-output path sends OpenAI's strict `json_schema` `response_format`
even for `deepseek/...` models, which DeepSeek's API currently rejects
outright ("This response_format type is unavailable now" -- a 400 on every
`output_pydantic` call, while a plain-text call on the same model/agent
succeeds). This is an upstream crewai/DeepSeek incompatibility, not specific
to any one pipeline's prompts -- it would break this agent identically if
`output_pydantic` were used here. Route around it the same way the
writer/editor pipelines already do: ask for raw JSON via the prompt
(`_json_output_instructions`, embedding the real Pydantic schema) and parse
it manually in `lib.crew.trends.execute`.
"""

from __future__ import annotations

import json

from crewai import Agent, Task
from crewai_tools import SerperDevTool
from pydantic import BaseModel

from lib.crew.trends.models import TrendsOutput

DEEPSEEK_MODEL = "deepseek/deepseek-chat"

_AGENT_BACKSTORY = (
    "You are a senior market-trend scout for a niche blog. You are grounded, "
    "specific, and never invent a trend signal -- every signal you surface is "
    "backed by either real Search Console ranking data already handed to you, "
    "a concrete thing you found via live web search, or a real pattern you "
    "read off the brand's Instagram trends feed when one is available. You "
    "know this brand's voice and audience and only surface signals that "
    "genuinely fit them. You do not draft post titles, angles, or final "
    "content ideas -- that synthesis is a separate agent's job downstream of "
    "you; your only output is a deduped list of keyword-level trend signals."
)


def _json_output_instructions(model: type[BaseModel]) -> str:
    """Explicit "respond with ONLY this JSON schema" instructions, embedding
    the model's real `model_json_schema()` -- more precise than a prose
    description, and the mechanism `lib.crew.trends.execute` relies on to
    manually parse+validate the raw response (see module docstring)."""
    schema = json.dumps(model.model_json_schema())
    return (
        "Respond with ONLY a single valid JSON object -- no markdown code fences, "
        "no commentary before or after it, no trailing text. The JSON MUST validate "
        f"against this JSON Schema:\n{schema}"
    )


def build_trends_agent(*, model: str = DEEPSEEK_MODEL) -> Agent:
    """One `Agent`: role = market trend scout, tools = live web search."""
    return Agent(
        role="Market Trend Scout",
        goal=(
            "Surface a deduped, keyword-level list of brand-relevant trend signals by "
            "merging pre-scored GSC ranking opportunities (carried forward as-is), fresh "
            "web-search findings, and -- when available -- Instagram trend-page content. "
            "Never synthesize final post titles or ideas; that is a separate agent's job."
        ),
        backstory=_AGENT_BACKSTORY,
        llm=model,
        tools=[SerperDevTool()],
        verbose=False,
        allow_delegation=False,
    )


def build_trends_task(agent: Agent, description: str) -> Task:
    """One `Task`. Structured output is enforced by manual JSON parsing in
    `lib.crew.trends.execute.execute_trends_crew` (see module docstring for
    why -- NOT `output_pydantic`)."""
    return Task(
        description=description,
        expected_output=(
            "A JSON object matching the TrendsOutput schema: a deduped list of trend "
            "signals, each with keyword, category, opportunity_type, score, and "
            f"reason.\n\n{_json_output_instructions(TrendsOutput)}"
        ),
        agent=agent,
    )
