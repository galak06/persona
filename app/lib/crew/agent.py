"""CrewAI `Agent`/`Task` construction for the content-opportunity scout.

One agent, one task -- no multi-agent handoff needed for this piece (see the
plan's "Known gaps" note: a second CrewAI pipeline would justify more
structure than this). `llm` is passed as the LiteLLM-style model string
`"deepseek/deepseek-chat"` directly to `Agent`, per the locked-in decision to
avoid a bespoke `DeepSeekLLM` adapter in `lib/llm_client.py`.

Verified against the installed `crewai==1.15.12`: this version does NOT
require (or even import) `litellm` for DeepSeek -- it ships a native
OpenAI-compatible provider (`crewai.llms.providers.openai_compatible`) that
maps the `"deepseek/..."` prefix to `https://api.deepseek.com/v1` and reads
`DEEPSEEK_API_KEY` from the environment automatically, same convention the
plan assumed LiteLLM would provide. `litellm` isn't installed in this repo at
all; nothing here imports it directly.
"""

from __future__ import annotations

from crewai import Agent, Task
from crewai_tools import SerperDevTool

from lib.crew.models import ScoutOutput

DEEPSEEK_MODEL = "deepseek/deepseek-chat"

_AGENT_BACKSTORY = (
    "You are a senior content strategist for a niche blog. You are grounded, "
    "specific, and never propose generic filler topics -- every idea you "
    "surface is backed by either real Search Console ranking data or a "
    "concrete thing you found via live web search. You know this brand's "
    "voice and audience and only propose ideas that genuinely fit."
)


def build_scout_agent(*, model: str = DEEPSEEK_MODEL) -> Agent:
    """One `Agent`: role = content opportunity scout, tools = live web search."""
    return Agent(
        role="Content Opportunity Scout",
        goal=(
            "Synthesize pre-scored GSC ranking opportunities with fresh web-search "
            "findings into a single, deduped, ranked list of content ideas grounded "
            "in this brand's real identity and audience."
        ),
        backstory=_AGENT_BACKSTORY,
        llm=model,
        tools=[SerperDevTool()],
        verbose=False,
        allow_delegation=False,
    )


def build_scout_task(agent: Agent, description: str) -> Task:
    """One `Task`, structured output enforced via `output_pydantic`."""
    return Task(
        description=description,
        expected_output=(
            "A JSON object matching the ScoutOutput schema: a list of ranked, "
            "deduped content ideas, each with topic, target_keyword, category, "
            "reasoning, opportunity_type, and priority_score."
        ),
        agent=agent,
        output_pydantic=ScoutOutput,
    )
