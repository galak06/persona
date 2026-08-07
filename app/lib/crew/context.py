"""Builds the compact brand-grounding context fed into the Trends and Idea
agents' prompts (`lib.crew.trends.prompts.build_trends_task_description` /
`lib.crew.idea.prompts.build_idea_task_description`).

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
