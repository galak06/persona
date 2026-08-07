"""Pydantic output contract for the CrewAI market-trend scout.

`lib.crew.trends.execute.execute_trends_crew` parses the agent's raw JSON
response and validates it against `TrendsOutput` directly (see that module's
docstring for why -- manual parsing, not `Task(output_pydantic=...)`).
`TrendsOutput` is the wrapper the parser validates against -- a bare
`list[TrendSignal]` isn't a `BaseModel`.

Deliberately minimal: `keyword`, `category`, `opportunity_type`, `score`,
`reason` only -- no `matched_query`/`best_position`/`impressions`. Mirrors
`lib.crew.context.serialize_opportunities`'s existing precedent of dropping
those raw GSC fields for LLM-facing contracts; the downstream Idea stage
only needs `score` plus a prose `reason`, not the raw ranking data behind it.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# Mirrors `lib.gsc_scout_scoring.GscOpportunity.opportunity_type` ("optimize" /
# "emerging" / "discovery"), plus "web_discovery" for signals found purely via
# live web search, plus "instagram_trend" for signals read off the brand's
# Instagram trends feed. Not imported from `lib.crew.models.OPPORTUNITY_TYPES`
# -- these are two independently-evolving pipeline stages (see
# `lib.crew.trends.agent`'s module docstring).
TREND_OPPORTUNITY_TYPES: tuple[str, ...] = (
    "optimize",
    "emerging",
    "discovery",
    "web_discovery",
    "instagram_trend",
)


class TrendSignal(BaseModel):
    """One surfaced trend signal -- carried forward from real GSC ranking
    data, discovered via live web search, or read off the Instagram trends
    feed. Keyword-level only; not yet a publishable post idea."""

    keyword: str = Field(description="The concrete keyword or topic this signal is about.")
    category: str = Field(description="Content category, e.g. 'Recipes', 'GPS/Gear', 'Health'.")
    opportunity_type: str = Field(
        description=f"One of: {', '.join(TREND_OPPORTUNITY_TYPES)}.",
    )
    score: float = Field(description="0-100 relative strength of this signal; higher = stronger.")
    reason: str = Field(
        description="Why this signal matters right now -- cite the GSC data, the search "
        "finding, or the Instagram trend text that grounds it."
    )


class TrendsOutput(BaseModel):
    """Wrapper `execute_trends_crew` validates the LLM's final answer against."""

    signals: list[TrendSignal] = Field(default_factory=list)
