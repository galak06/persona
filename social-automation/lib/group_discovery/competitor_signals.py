"""Competitor-driven group discovery.

Loads a list of content competitors (pet blogs / influencer pages) and uses their
names as Facebook group-search queries. Any group surfaced by a competitor query
is also scanned for mentions of OTHER competitors — multiple competitors in the
same group is a strong signal it's worth joining.
"""

from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPETITORS_FILE = PROJECT_ROOT / "data/competitors.json"


def load_competitors() -> list[dict]:
    """Return the active competitors from data/competitors.json."""
    if not COMPETITORS_FILE.exists():
        return []
    try:
        data = json.loads(COMPETITORS_FILE.read_text())
    except Exception:
        return []
    return [c for c in data.get("competitors", []) if c.get("active", True)]


def active_queries(competitors: list[dict] | None = None) -> list[str]:
    """Return FB group-search queries derived from active competitors."""
    competitors = competitors if competitors is not None else load_competitors()
    seen: set[str] = set()
    out: list[str] = []
    for c in competitors:
        q = (c.get("fb_query") or c.get("name") or "").strip()
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out


def _normalize(s: str) -> str:
    return " ".join(s.lower().split())


def count_mentions(group_text: str, competitors: list[dict] | None = None) -> tuple[int, list[str]]:
    """Count distinct competitor names appearing in group text.

    Returns (count, matched_names). Uses substring match on normalized text.
    """
    competitors = competitors if competitors is not None else load_competitors()
    text = _normalize(group_text)
    matched: list[str] = []
    for c in competitors:
        name = (c.get("name") or "").strip()
        query = (c.get("fb_query") or "").strip()
        needle_name = _normalize(name) if name else ""
        needle_query = _normalize(query) if query else ""
        hit = False
        if (needle_name and needle_name in text) or (needle_query and needle_query in text):
            hit = True
        if hit:
            matched.append(name or query)
    return len(matched), matched


def annotate_with_mentions(candidates: list[dict], competitors: list[dict] | None = None) -> None:
    """In-place: add competitor_mentions + competitor_names to each candidate."""
    competitors = competitors if competitors is not None else load_competitors()
    for g in candidates:
        text = f"{g.get('name', '')} {g.get('description', '')} {g.get('url', '')}"
        count, matched = count_mentions(text, competitors)
        g["competitor_mentions"] = count
        g["competitor_names"] = matched
