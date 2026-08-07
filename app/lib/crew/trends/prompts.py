"""Task-description ("prompt") builder for the market-trend scout agent.

Split out into its own module purely for file-size discipline, matching
`lib.crew.writer.prompts`/`lib.crew.editor.prompts`'s role for their own
stages. References the same field names `lib.crew.context.serialize_opportunities`
actually serializes (keyword, category, opportunity_type, score, best_position,
impressions, reason) so this prompt's instructions stay honest about what the
agent is actually looking at.
"""

from __future__ import annotations


def build_trends_task_description(
    *,
    identity: str,
    seed_keywords: str,
    opportunities_json: str,
    data_sufficient: bool,
    instagram_trends_text: str | None,
) -> str:
    """The trend scout agent's full prompt: GSC opportunities + live web
    search + (optional) Instagram trend text -> a deduped `TrendsOutput`.

    Explicitly NOT responsible for post titles or brand voice framing --
    keyword-level signal surfacing only; synthesis into publishable ideas
    happens downstream in `lib.crew.idea`.
    """
    sufficiency_note = (
        "GSC history is sufficient for this brand -- 'optimize'/'emerging' "
        "opportunities below reflect real ranking data."
        if data_sufficient
        else "GSC history is NOT yet sufficient for this brand -- treat every "
        "GSC-derived opportunity below as topic discovery, not ranking optimization."
    )
    instagram_section = (
        f"## Instagram trends feed (raw visible text from instagram.com/popular/instagram-trends/)\n"
        f"{instagram_trends_text}"
        if instagram_trends_text
        else "## Instagram trends feed\n(Instagram trends unavailable this run)"
    )
    return f"""You are surfacing market-trend signals for this brand. You are NOT writing \
post titles or final content ideas -- only keyword-level signals that a separate agent \
will later synthesize into publishable topics.

## Brand context
{identity}

## Already-targeted seed keywords (do not just restate these as "new" signals)
{seed_keywords or "(none on file)"}

## Pre-computed GSC opportunities (real Search Console data, already scored -- \
use directly, do not re-derive)
{sufficiency_note}
{opportunities_json}

{instagram_section}

## Your job
1. Carry forward the GSC opportunities above AS-IS into signals -- keep their real \
keyword, category, score, and reason; keep opportunity_type matching the source data \
("optimize"/"emerging"/"discovery"). Do not re-derive or re-score them.
2. Use your web search tool to find genuinely NEW trend signals this brand hasn't \
covered yet -- trending discussions, "people also ask"-style questions, and competitor \
content gaps relevant to this brand's niche and audience. Do not just re-search the \
seed keywords verbatim; look for adjacent, timely, or underserved angles. Tag these \
opportunity_type: "web_discovery".
3. If the Instagram trends feed text above is present, extract any brand-relevant \
content-format or topic signals from it (recurring themes, formats, or subjects that fit \
this brand's niche and audience) and add them as signals tagged opportunity_type: \
"instagram_trend", with `reason` citing the specific thing you read in that text. If the \
Instagram trends feed is unavailable (noted above), skip this source entirely -- do not \
invent Instagram signals.
4. Merge all sources into one flat, deduped list -- drop near-duplicate keywords (same \
topic phrased differently) rather than keeping both. Every signal must include a concrete \
keyword and a reason grounded in either the GSC data, a specific web search finding, or \
the Instagram trends text -- never a generic, made-up justification.
"""
