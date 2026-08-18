"""Task-description ("prompt") builder for the content-idea synthesis agent.

Split out of `lib.crew.idea.agent` purely for file-size discipline, same role
`lib.crew.editor.prompts`/`lib.crew.writer.prompts` play for their agents.

`serialize_trend_signals` turns `lib.crew.trends`'s already-gathered,
already-scored signals into the compact JSON this agent's prompt embeds --
the Idea agent uses these directly rather than re-deriving score/
opportunity_type itself, same "real data over hallucination" posture
`lib.crew.context.serialize_opportunities` established for the old combined
scout.
"""

from __future__ import annotations

import json

from lib.crew.trends.models import TrendSignal


def serialize_trend_signals(signals: list[TrendSignal]) -> str:
    """Compact JSON of the already-gathered, already-scored trend signals
    (`lib.crew.trends`'s output) -- real signal data the agent should use
    directly, not re-derive or hallucinate."""
    rows = [
        {
            "keyword": s.keyword,
            "category": s.category,
            "opportunity_type": s.opportunity_type,
            "score": s.score,
            "reason": s.reason,
        }
        for s in signals
    ]
    return json.dumps(rows, ensure_ascii=False)


def serialize_existing_topics(topics: set[str], *, limit: int = 120) -> str:
    """Bulleted list of topics already in `content_ideas`, for the prompt.

    Sorted for determinism (the source is an unordered set) and capped: the
    table grows without bound, and at a few hundred rows an uncapped dump
    would crowd out the trend signals this agent is actually meant to
    synthesize from. The count of what was omitted is kept so the agent
    knows the list is partial rather than exhaustive.
    """
    if not topics:
        return "(nothing published or queued yet)"
    ordered = sorted(topics)
    shown = ordered[:limit]
    lines = "\n".join(f"- {t}" for t in shown)
    remaining = len(ordered) - len(shown)
    if remaining > 0:
        lines += f"\n- (+{remaining} more not listed)"
    return lines


def build_idea_task_description(
    *,
    identity: str,
    voice: str,
    seed_keywords: str,
    trends_json: str,
    existing_topics: str,
    top_n: int,
) -> str:
    """The full prompt handed to the idea-synthesis agent's `Task`.

    Combines: compact brand grounding, the already-gathered trend signals
    (so the agent doesn't need to re-derive score/opportunity_type or go
    search anything itself), the existing keyword seed list (so ideas aren't
    just seed keywords restated verbatim as "new" topics), and the topics
    already in `content_ideas`.

    That last one is the difference between avoiding a repeat and detecting
    one after the fact. The agent used to be handed a fixed seed vocabulary
    and no memory, so it re-derived the same obvious high-value angles every
    run, and a post-hoc string filter was left to clean up -- which it could
    not do, because the agent rewrites the headline each time. Telling it
    what exists makes non-repetition a generation constraint; the filter in
    `lib.crew.topic_similarity` stays as the backstop for what slips past.
    """
    return f"""You are synthesizing brand-voiced, publishable blog content ideas from \
already-gathered trend signals. You do NOT have web search -- work only from the \
signals below.

## Brand context
{identity}

## Brand voice (every idea's reasoning must fit this voice)
{voice or "(no voice guide available)"}

## Already-targeted seed keywords (do not restate these verbatim as "new" ideas)
{seed_keywords or "(none on file)"}

## Topics ALREADY covered -- do not propose these again, in any rewording
{existing_topics}

## Trend signals (already gathered and scored -- use directly, do not re-derive)
{trends_json}

## Your job
1. For each trend signal worth pursuing, synthesize a concrete, publishable topic/title \
(not just the bare keyword), a target_keyword, a category, and a reasoning that explains \
why this fits this brand's voice and audience -- grounded in the signal's own `reason`, \
never a generic, made-up justification.
2. Carry forward each signal's `opportunity_type` unchanged, and re-rank a fresh \
priority_score (0-100) reflecting this brand's real fit for that signal, not just the \
signal's own raw score.
3. Dedupe near-identical topics (same idea phrased differently), and drop any idea that is \
just a seed keyword restated verbatim with no real synthesis.
4. Check every candidate against the "already covered" list above and DROP it if the \
subject matches, even when your headline is completely different. "Pumpkin vs Sweet Potato \
for Dog Digestion" and "Sweet Potato vs. Pumpkin for Dogs" are the SAME topic. A new angle \
on a covered subject only counts as new if it answers a materially different question -- \
a fresh hook, season, or product name on the same comparison does not.
5. Prefer subjects with no coverage yet over a stronger signal on a subject already \
covered. Breadth is the point: this brand's ideas skew heavily toward a few repeated \
themes, so an unexplored area at a moderate score beats a fifth take on a saturated one.
6. Return at most {top_n} ideas, highest priority_score first. This is pure synthesis --
never invent a trend signal that isn't grounded in the list above, and never perform a web \
search (you have none available).
"""
