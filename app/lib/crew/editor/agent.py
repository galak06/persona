"""CrewAI `Agent`/`Task` construction for the quality-editor gate.

One agent, one task: scores an already-assembled post (title + body_html)
against a fixed rubric and returns a `QualityVerdict`. Same
`llm="deepseek/..."` wiring as `lib.crew.writer.agent` -- `DEEPSEEK_MODEL` is
redefined locally rather than imported, for the same "independently-evolving
pipeline" reason `lib.crew.writer.agent` gives for not importing
`lib.crew.agent`'s constant.

No tools: the editor works only from the post text and the brand voice guide
it's given in the prompt, never a live search.

Uses manual JSON parsing (`lib.crew.editor.execute`), NOT
`Task(output_pydantic=...)` -- confirmed via a live, isolated test this build
(see `lib.crew.editor.execute`'s module docstring) that `crewai==1.15.12`'s
structured-output path still sends OpenAI's strict `json_schema`
`response_format` for `deepseek/...` models, which DeepSeek's API rejects
(the same upstream incompatibility `lib.crew.writer.agent` already documents
for the strategist/writer pair -- confirmed here independently rather than
assumed, since `QualityVerdict`'s schema is a different shape).
"""

from __future__ import annotations

import json

from crewai import Agent, Task
from pydantic import BaseModel

from lib.crew.editor.models import QualityVerdict

DEEPSEEK_MODEL = "deepseek/deepseek-chat"

_EDITOR_BACKSTORY = (
    "You are a strict, senior copy editor for a niche blog. Nobody reads a draft "
    "before it becomes a WordPress post, so you are the only quality gate standing "
    "between an LLM-drafted post and publication. You score prose the way a skeptical "
    "editor-in-chief would: coherent, on-brand, structurally complete against its "
    "brief, and completely free of any sign that it was written by an AI that left "
    "its own reasoning in the copy. You are not a generic grammar checker -- you are "
    "specifically hunting for stray LLM artifacts, because that failure mode is the "
    "single most damaging thing that can reach a live site unreviewed."
)


def _json_output_instructions(model: type[BaseModel]) -> str:
    """Same mechanism as `lib.crew.writer.agent._json_output_instructions`
    (kept as an independent copy, not a shared import -- see that module's
    docstring on why this pipeline avoids cross-imports between stages)."""
    schema = json.dumps(model.model_json_schema())
    return (
        "Respond with ONLY a single valid JSON object -- no markdown code fences, "
        "no commentary before or after it, no trailing text. The JSON MUST validate "
        f"against this JSON Schema:\n{schema}"
    )


def build_editor_agent(*, model: str = DEEPSEEK_MODEL) -> Agent:
    """One `Agent`: role = quality editor, no tools (works from the post text only)."""
    return Agent(
        role="Quality Editor",
        goal=(
            "Score one assembled blog post against a fixed rubric -- coherence, "
            "on-brand voice, structural completeness against its outline, and "
            "absence of stray LLM artifacts -- and return a precise, debuggable "
            "verdict. Reject anything that reads like unfinished AI output."
        ),
        backstory=_EDITOR_BACKSTORY,
        llm=model,
        tools=[],
        verbose=False,
        allow_delegation=False,
    )


def build_editor_task(agent: Agent, description: str) -> Task:
    """One `Task`. Structured output is enforced by manual JSON parsing in
    `lib.crew.editor.execute.execute_editor_crew` -- NOT `output_pydantic`
    (see module docstring)."""
    return Task(
        description=description,
        expected_output=(
            "A JSON object matching the QualityVerdict schema: score, passed, issues, "
            f"and stray_artifacts.\n\n{_json_output_instructions(QualityVerdict)}"
        ),
        agent=agent,
    )
