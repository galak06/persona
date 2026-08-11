"""CrewAI `Agent`/`Task` construction for the Social Post Writer stage.

`DEEPSEEK_MODEL` and `_json_output_instructions` are redefined locally rather
than imported from a sibling package -- matches this repo's
"independently-evolving pipelines" convention (`lib.crew.reels.agent`,
`lib.crew.idea.agent`, `lib.crew.categorizer.agent` all keep their own copies;
see the last one's docstring, and `lib.crew.writer.agent`'s on why a shared
model constant would silently couple unrelated pipelines' model choice).

Deliberately does NOT use `Task(output_pydantic=...)` -- same DeepSeek/CrewAI
`response_format` incompatibility documented in `lib.crew.idea.agent`'s module
docstring: `crewai==1.15.12` sends OpenAI's strict `json_schema` response
format even for `deepseek/...` models, which DeepSeek's API rejects outright.
Ask for raw JSON via the prompt (`_json_output_instructions`) and parse it
manually in `lib.crew.socialpost.execute`.
"""

from __future__ import annotations

import json

from crewai import Agent, Task
from pydantic import BaseModel

from lib.crew.socialpost.models import SocialPostPlan

DEEPSEEK_MODEL = "deepseek/deepseek-chat"

_AGENT_BACKSTORY = (
    "You are a social editor for a niche blog's Facebook Page and Instagram "
    "account. You write posts that answer a real question in the first line -- "
    "the way someone would actually ask it -- because that is what gets read "
    "when the caption is cut off after two lines and what gets quoted when an "
    "AI assistant answers that same question. You know this brand's voice, you "
    "write differently for each platform because their link rules differ, and "
    "you never invent a claim the source post doesn't actually make."
)


def _json_output_instructions(model: type[BaseModel]) -> str:
    """Explicit "respond with ONLY this JSON schema" instructions, embedding
    the model's real `model_json_schema()` -- same mechanism
    `lib.crew.reels.agent._json_output_instructions` uses."""
    schema = json.dumps(model.model_json_schema())
    return (
        "Respond with ONLY a single valid JSON object -- no markdown code fences, "
        "no commentary before or after it, no trailing text. The JSON MUST validate "
        f"against this JSON Schema:\n{schema}"
    )


def build_social_post_agent(*, model: str = DEEPSEEK_MODEL) -> Agent:
    """One `Agent`: role = social post writer, no tools (works only from the
    post content it's given -- never a live search)."""
    return Agent(
        role="Social Post Writer",
        goal=(
            "Turn a published blog post into one Facebook Page caption and one "
            "Instagram caption -- each answering a real search question in its first "
            "sentence -- plus the art direction for the single image they share, "
            "written to earn follows and send readers to the site."
        ),
        backstory=_AGENT_BACKSTORY,
        llm=model,
        tools=[],
        verbose=False,
        allow_delegation=False,
    )


def build_social_post_task(agent: Agent, description: str) -> Task:
    """One `Task`. Structured output is enforced by manual JSON parsing in
    `lib.crew.socialpost.execute.execute_social_post_crew` (see docstring)."""
    return Task(
        description=description,
        expected_output=(
            "A JSON object matching the SocialPostPlan schema: target_question, "
            "fb_caption, ig_caption, overlay_headline, overlay_subcopy, image_brief, "
            "cta_ribbon_text and image_alt_text."
            f"\n\n{_json_output_instructions(SocialPostPlan)}"
        ),
        agent=agent,
    )
