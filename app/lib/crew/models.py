"""Pydantic output contract for the CrewAI content-idea synthesis stage.

`lib.crew.idea.execute.execute_idea_crew` parses the idea-synthesis agent's
raw JSON response and validates it against `ScoutOutput` directly (manual
parsing, not `Task(output_pydantic=...)` -- see `lib.crew.idea.agent`'s
module docstring for why). `ScoutOutput` is the wrapper that parser
validates against -- a bare `list[IdeaCandidate]` isn't a `BaseModel`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# Mirrors `lib.gsc_scout_scoring.GscOpportunity.opportunity_type` ("optimize" /
# "emerging" / "discovery") plus "web_discovery" for ideas that came purely
# from live web search with no GSC signal behind them, and "instagram_trend"
# for ideas synthesized from the Instagram trends feed (`lib.crew.trends`).
OPPORTUNITY_TYPES: tuple[str, ...] = (
    "optimize",
    "emerging",
    "discovery",
    "web_discovery",
    "instagram_trend",
)


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
