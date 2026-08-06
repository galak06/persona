"""CrewAI `Agent`/`Task` construction for the content strategist + writer pair.

Two agents, two sequential tasks (strategist's `ContentBrief` output feeds
the writer's prompt -- see `lib.crew.writer.orchestrator`), same
`llm="deepseek/..."` string wiring as `lib.crew.agent` (confirmed there:
installed `crewai==1.15.12` needs no LiteLLM/`lib.llm_client` involvement for
DeepSeek). `DEEPSEEK_MODEL` is redefined locally rather than imported from
`lib.crew.agent` -- these are two independently-evolving pipelines (topic
discovery vs. drafting); a shared constant would silently couple their model
choice.

Neither agent gets tools (no `SerperDevTool`): the strategist works from a
brief made of already-real data (the idea, brand voice, and the site's own
content cache -- never a live search), and the writer works only from the
strategist's brief. This is a deliberate difference from `lib.crew.agent`'s
scout, which needs live web search.

Deliberately does NOT use `Task(output_pydantic=...)` (unlike `lib.crew.agent`'s
scout): live-tested against DeepSeek this build and confirmed (with a minimal,
isolated repro using the scout's exact `output_pydantic` pattern) that
`crewai==1.15.12`'s structured-output path sends OpenAI's strict `json_schema`
`response_format` even for `deepseek/...` models, which DeepSeek's API
currently rejects outright ("This response_format type is unavailable now" --
a 400 on literally every `output_pydantic` call, while a plain-text call on
the same model/agent succeeds). This is an upstream crewai/DeepSeek
incompatibility, not specific to this pipeline's prompts -- it would break the
scout identically if re-run right now. Route around it instead: ask for raw
JSON via the prompt (`_json_output_instructions`, embedding the real Pydantic
schema) and parse it manually in `lib.crew.writer.execute`.
"""

from __future__ import annotations

import json

from crewai import Agent, Task
from pydantic import BaseModel

from lib.crew.writer.models import ContentBrief, WrittenPost

DEEPSEEK_MODEL = "deepseek/deepseek-chat"

_STRATEGIST_BACKSTORY = (
    "You are a senior content strategist for a niche blog. You turn one approved "
    "content idea into a tight, publishable brief: a title, an outline, target "
    "keywords, real internal-link candidates, and a handful of FAQ questions the "
    "post should answer. You never invent URLs -- internal link candidates must "
    "come only from the real recent-posts list you're given. You ground the "
    "brand's mascot/voice angle in the idea's own reasoning, not a generic take."
)

_WRITER_BACKSTORY = (
    "You are a senior blog writer who writes in a specific brand's voice: a "
    "data-driven, first-person, engineer-turned-dog-owner persona. You follow a "
    "brief exactly -- its outline, its keywords, its FAQ questions, its internal "
    "link candidates -- and produce full, publication-ready WordPress HTML. You "
    "never invent product links or affiliate keys outside the real catalog "
    "you're given, and you never invent internal links outside the brief's "
    "candidates. You never make medical, credential, or absolute-health claims."
)


def _json_output_instructions(model: type[BaseModel]) -> str:
    """Explicit "respond with ONLY this JSON schema" instructions, embedding
    the model's real `model_json_schema()` -- more precise than a prose
    description, and the mechanism `lib.crew.writer.execute` relies on to
    manually parse+validate the raw response (see module docstring)."""
    schema = json.dumps(model.model_json_schema())
    return (
        "Respond with ONLY a single valid JSON object -- no markdown code fences, "
        "no commentary before or after it, no trailing text. The JSON MUST validate "
        f"against this JSON Schema:\n{schema}"
    )


def build_strategist_agent(*, model: str = DEEPSEEK_MODEL) -> Agent:
    """One `Agent`: role = content strategist, no tools (works from real brand data only)."""
    return Agent(
        role="Content Strategist",
        goal=(
            "Turn one approved content idea into a structured, publishable brief: "
            "title (with current year), outline, keywords, real internal-link "
            "candidates, FAQ questions, and a brand-voice angle grounded in the "
            "idea's own reasoning."
        ),
        backstory=_STRATEGIST_BACKSTORY,
        llm=model,
        tools=[],
        verbose=False,
        allow_delegation=False,
    )


def build_strategist_task(agent: Agent, description: str) -> Task:
    """One `Task`. Structured output is enforced by manual JSON parsing in
    `lib.crew.writer.execute.execute_strategist_crew` (see module docstring
    for why -- NOT `output_pydantic`, unlike `lib.crew.agent`'s scout)."""
    return Task(
        description=description,
        expected_output=(
            "A JSON object matching the ContentBrief schema: suggested_title, outline, "
            "primary_keyword, secondary_keywords, internal_link_candidates (title+url "
            "pairs taken only from the real candidates given), faq_questions, and "
            f"mascot_angle.\n\n{_json_output_instructions(ContentBrief)}"
        ),
        agent=agent,
    )


def build_writer_agent(*, model: str = DEEPSEEK_MODEL) -> Agent:
    """One `Agent`: role = blog post writer, no tools (works from the brief only)."""
    return Agent(
        role="Blog Post Writer",
        goal=(
            "Write a full, publication-ready blog post in HTML that follows the "
            "given brief and brand voice exactly -- byline, affiliate disclosure, "
            "mascot-story hook, H2/H3 body per the outline, an honest product "
            "comparison table ONLY if real catalog products fit the topic, an FAQ "
            "section answering the brief's questions, related-reading links using "
            "only the brief's real internal-link candidates, and a closing pick."
        ),
        backstory=_WRITER_BACKSTORY,
        llm=model,
        tools=[],
        verbose=False,
        allow_delegation=False,
    )


def build_writer_task(agent: Agent, description: str) -> Task:
    """One `Task`. Structured output is enforced by manual JSON parsing in
    `lib.crew.writer.execute.execute_writer_crew` (see module docstring for
    why -- NOT `output_pydantic`, unlike `lib.crew.agent`'s scout)."""
    return Task(
        description=description,
        expected_output=(
            "A JSON object matching the WrittenPost schema: title, body_html (full "
            "WordPress-ready HTML), word_count, faq_pairs, internal_links_used, and "
            f"affiliate_keys_used.\n\n{_json_output_instructions(WrittenPost)}"
        ),
        agent=agent,
    )
