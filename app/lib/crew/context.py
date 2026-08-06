"""Builds the compact brand-grounding context fed into the scout agent's prompt.

Deliberately summarizes rather than dumps whole files into the prompt -- the
brand voice guide can run to a few hundred lines; the LLM needs a "who is
this brand" anchor, not the full document. GSC opportunities, on the other
hand, are real structured data already computed by `lib.gsc_scout_scoring`
(read, not re-derived here) and are serialized in full so the LLM never has
to guess at ranking data it can't see.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.gsc_scout_scoring import GscOpportunity, KeywordSeed

_VOICE_GUIDE_MAX_CHARS = 1200

# Same current-path-first / legacy-fallback pattern as
# `lib.gsc_scout.load_site_content_cache` -- the 2026-06 reorg moved brand
# config docs under `data/config/`, but `config.json`'s own `file_paths`
# block (and some older readers) still point at the pre-reorg path.
_CURRENT_VOICE_GUIDE = Path("data") / "config" / "brand_voice_guide.md"
_LEGACY_VOICE_GUIDE = Path("data") / "brand_voice_guide.md"


def brand_identity_summary(config: dict[str, Any]) -> str:
    """One-paragraph "who is this brand" anchor from `config.json`'s `site` block."""
    site = config.get("site", {}) if isinstance(config, dict) else {}
    name = str(site.get("name") or "").strip()
    persona = str(site.get("brand_persona") or "").strip()
    mascot = str(site.get("mascot_name") or "").strip()
    niche = str(site.get("niche") or "").strip()
    audience = str(site.get("target_audience") or "").strip()

    lines = [f"Brand: {name}" + (f" (voice: {persona})" if persona else "")]
    if mascot:
        lines.append(f"Mascot / recurring character: {mascot}")
    if niche:
        lines.append(f"Niche: {niche}")
    if audience:
        lines.append(f"Target audience: {audience}")
    return "\n".join(lines)


def brand_voice_summary(brand_dir: Path, *, max_chars: int = _VOICE_GUIDE_MAX_CHARS) -> str:
    """Truncated excerpt of `brand_voice_guide.md` -- current path first, legacy
    fallback, matching `lib.gsc_scout.load_site_content_cache`'s convention.

    Returns "" if neither path exists, so callers can drop the section
    entirely rather than send an empty header.
    """
    for rel in (_CURRENT_VOICE_GUIDE, _LEGACY_VOICE_GUIDE):
        candidate = brand_dir / rel
        if candidate.exists():
            text = candidate.read_text(encoding="utf-8").strip()
            if len(text) <= max_chars:
                return text
            return text[:max_chars].rsplit("\n", 1)[0] + "\n...(truncated)"
    return ""


def seed_keywords_summary(seeds: list[KeywordSeed]) -> str:
    """Comma-joined list of already-onboarded seed keywords, so the agent's web
    search targets genuinely new topics rather than re-proposing these."""
    return ", ".join(sorted({s.keyword for s in seeds}))


def serialize_opportunities(opportunities: list[GscOpportunity]) -> str:
    """Compact JSON of the pre-computed, real GSC-grounded opportunities
    (`lib.gsc_scout_scoring.rank_opportunities`'s output) -- real ranking
    data the agent should use directly, not re-derive or hallucinate."""
    rows = [
        {
            "keyword": o.keyword,
            "category": o.category,
            "opportunity_type": o.opportunity_type,
            "score": o.score,
            "best_position": o.best_position,
            "impressions": o.impressions,
            "reason": o.reason,
        }
        for o in opportunities
    ]
    return json.dumps(rows, ensure_ascii=False)


def build_task_description(
    *,
    identity: str,
    voice: str,
    seed_keywords: str,
    opportunities_json: str,
    data_sufficient: bool,
    top_n: int,
) -> str:
    """The full prompt handed to the scout agent's `Task`.

    Combines: compact brand grounding, the already-scored GSC opportunities
    (so the agent doesn't need to re-derive ranking signal), the existing
    keyword seed list (so web search targets genuinely new topics), and the
    output contract (`lib.crew.models.ScoutOutput`, enforced separately via
    `Task(output_pydantic=...)`).
    """
    sufficiency_note = (
        "GSC history is sufficient for this brand -- 'optimize'/'emerging' "
        "opportunities below reflect real ranking data."
        if data_sufficient
        else "GSC history is NOT yet sufficient for this brand -- treat every "
        "GSC-derived opportunity below as topic discovery, not ranking optimization."
    )
    return f"""You are scoring and discovering blog content opportunities for this brand.

## Brand context
{identity}

## Brand voice (write reasoning that fits this voice; do not draft the post itself)
{voice or "(no voice guide available)"}

## Already-targeted seed keywords (do not just restate these as "new" ideas)
{seed_keywords or "(none on file)"}

## Pre-computed GSC opportunities (real Search Console data, already scored -- \
use directly, do not re-derive)
{sufficiency_note}
{opportunities_json}

## Your job
1. Review the GSC opportunities above and carry forward the strongest ones as
   candidates (opportunity_type: "optimize" or "emerging" or "discovery", matching
   the source data).
2. Use your web search tool to find genuinely NEW topic ideas this brand hasn't
   covered yet -- trending discussions, "people also ask"-style questions, and
   competitor content gaps relevant to this brand's niche and audience. Do not
   just re-search the seed keywords verbatim; look for adjacent, timely, or
   underserved angles. Tag these opportunity_type: "web_discovery".
3. Merge both sources into one final list, drop near-duplicates (same topic
   phrased differently), and rank by real value to this brand's audience.
4. Return at most {top_n} ideas, highest priority_score first. Every idea must
   include a concrete target_keyword and a reasoning grounded in either the GSC
   data above or a specific thing you found via search -- never a generic,
   made-up justification.
"""
