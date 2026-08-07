"""WordPress tag resolution for the CrewAI content pipeline.

Mirrors `lib.crew.draft_category.resolve_category_id`'s "resolve real WP
term IDs, never guess" shape, but for tags: WordPress tags are cheap and
expected to be created on demand (unlike categories, which are a fixed
taxonomy this pipeline never invents), so each name is looked up by slug
and created if missing -- modeled on
`recipe-publisher/publishers/wordpress_ideas.py::_resolve_tags`.

Best-effort per tag: a single failed lookup/create is logged and skipped,
never raised -- tags are cosmetic metadata, not required for a
correctly-rendering draft (same convention as the hero-image step in
`lib.crew.draft`).
"""

from __future__ import annotations

import httpx

from lib.observability import get_logger

logger = get_logger(__name__)


def _tag_slug(name: str) -> str:
    return name.strip().lower().replace(" ", "-")


def resolve_tag_ids(client: httpx.Client, tag_names: list[str], idea_id: str) -> list[int]:
    """Resolve free-text tag names to real WP term IDs, creating any that
    don't already exist. Blank names are skipped; duplicate slugs (after
    normalization) are deduped, preserving first-seen order."""
    ids: list[int] = []
    seen_slugs: set[str] = set()
    for raw_name in tag_names:
        name = raw_name.strip()
        if not name:
            continue
        slug = _tag_slug(name)
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        try:
            existing = client.get("/wp-json/wp/v2/tags", params={"slug": slug}).json()
            if existing:
                ids.append(int(existing[0]["id"]))
                continue
            resp = client.post("/wp-json/wp/v2/tags", json={"name": name, "slug": slug})
            if resp.status_code >= 400:
                logger.warning(
                    "crew_draft_tag_create_failed",
                    idea_id=idea_id,
                    tag=name,
                    status_code=resp.status_code,
                )
                continue
            ids.append(int(resp.json()["id"]))
        except Exception as exc:
            logger.warning(
                "crew_draft_tag_resolve_failed", idea_id=idea_id, tag=name, error=str(exc)
            )
    return ids
