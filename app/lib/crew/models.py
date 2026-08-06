"""Pydantic output contract for the CrewAI content-opportunity scout.

Passed to `Task(output_pydantic=...)` so the LLM's final answer comes back
as structured data (`lib.crew.agent`) instead of free text that would need
brittle parsing. `ScoutOutput` is the wrapper CrewAI actually validates
against -- a bare `list[IdeaCandidate]` isn't a `BaseModel` and CrewAI's
`output_pydantic` requires one.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# Mirrors `lib.gsc_scout_scoring.GscOpportunity.opportunity_type` ("optimize" /
# "emerging" / "discovery") plus one new value for ideas that came purely from
# live web search with no GSC signal behind them at all.
OPPORTUNITY_TYPES: tuple[str, ...] = ("optimize", "emerging", "discovery", "web_discovery")


class IdeaCandidate(BaseModel):
    """One synthesized content idea -- either grounded in real GSC signal
    (carried over from `lib.gsc_scout_scoring.GscOpportunity`) or discovered
    fresh via the agent's web search."""

    topic: str = Field(description="A concrete, publishable blog post title/topic.")
    target_keyword: str = Field(description="The primary SEO keyword this post should target.")
    category: str = Field(description="Content category, e.g. 'Recipes', 'GPS/Gear', 'Health'.")
    reasoning: str = Field(
        description="Why this is a good opportunity right now -- cite GSC data or the "
        "search finding that grounds it."
    )
    opportunity_type: str = Field(
        description=f"One of: {', '.join(OPPORTUNITY_TYPES)}.",
    )
    priority_score: float = Field(description="0-100 relative priority; higher = do first.")


class ScoutOutput(BaseModel):
    """Wrapper `Task(output_pydantic=...)` validates the LLM's final answer against."""

    ideas: list[IdeaCandidate] = Field(default_factory=list)
