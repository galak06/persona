"""Row/config helpers for the CrewAI scout, split out of `lib.crew.scout`.

Pure functions with no crew or I/O orchestration in them: reading the brand
config, and shaping one `IdeaCandidate` into the dict `ideas_db.insert_idea`
expects. Extracted purely for file-size discipline once `scout.py` crossed
the project's 300-line limit -- same role `lib.crew.idea.prompts` plays for
`lib.crew.idea.agent`. No behaviour change; `scout.py` imports both back.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.crew.models import IdeaCandidate
from lib.observability import get_logger

logger = get_logger(__name__)

# An opportunity type in this set is exploratory rather than ranking-driven,
# so its row is tagged `topic_discovery` instead of `seo_traffic`.
DISCOVERY_TYPES = frozenset({"discovery", "web_discovery", "instagram_trend"})


def read_config(brand_dir: Path) -> dict[str, Any]:
    """`config.json`, tolerant of a missing/malformed file (never raises) --
    a small local copy of `lib.gsc_scout`'s private `_read_json` rather than
    importing that module's underscore-prefixed helper directly."""
    path = brand_dir / "config.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("crew_scout_config_json_parse_failed", path=str(path))
        return {}
    return data if isinstance(data, dict) else {}


def idea_row(idea: IdeaCandidate, *, data_sufficient: bool) -> dict[str, Any]:
    """One `content_ideas` row from a synthesized idea candidate."""
    return {
        "category": idea.category,
        "topic": idea.topic.strip(),
        "target_keyword": idea.target_keyword,
        # Same key `ideas_db.insert_idea` reads as `lib.gsc_scout._idea_row`
        # uses -- kept identical here so both scouts produce one row shape.
        "persona_context": idea.reasoning,
        "post_goal": "topic_discovery"
        if idea.opportunity_type in DISCOVERY_TYPES
        else "seo_traffic",
        "status": "publish",
        "input": json.dumps(
            {
                "source": "crewai_scout",
                "opportunity_type": idea.opportunity_type,
                "priority_score": idea.priority_score,
                "reasoning": idea.reasoning,
                "data_sufficient": data_sufficient,
            }
        ),
    }
