"""Site content cache — the brand's own published posts, from the WP REST API.

`data/cache/site_content_cache.json` is what grounds internal-link choice
(`lib.crew.writer.context`), reply drafting (`lib.reply_drafter`) and the GSC
scout. Until now nothing in code wrote it: the `site-analyzer` skill did, as
agent choreography, so the file had no freshness guarantee, ignored its own
`content_analysis.site_cache_max_posts` setting, and in practice held a
fraction of the site months out of date. A post can only link to something
present in this file, so a short stale cache silently caps how coherent a
category's internal linking can ever be.

This module is that missing writer: paginated over the real REST API, capped
by the brand's own configured `site_cache_max_posts`, category and tag IDs
resolved to the names every reader expects, written atomically.

Deliberately NOT re-emitted: `keywords_found`, `reviewed_products` and
`social_media`, which the skill used to write and which have no reader
anywhere in the codebase. `content_summary` IS preserved from any existing
cache -- `lib.keyword_research` reads `content_summary.site.content_gaps`,
and a crawl cannot regenerate it.
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from lib.io.jsonio import write_json
from lib.observability import get_logger

logger = get_logger(__name__)

_PER_PAGE = 100
_TAG_RE = re.compile(r"<[^>]+>")
_EXCERPT_MAX_CHARS = 400

# Only the fields a reader actually consumes. `content` is deliberately not
# requested: it would multiply the payload for every post to compute a
# `word_count` nothing reads.
_POST_FIELDS = "id,link,title,excerpt,date_gmt,categories,tags"


def _plain_text(raw: Any) -> str:
    """WP `*.rendered` HTML -> plain text: tags stripped, entities decoded.

    Both steps matter and in this order: excerpts arrive as `<p>` -wrapped
    HTML containing entities like `&#8217;`, and a reader that puts the raw
    string into a prompt would otherwise carry markup and mojibake into it.
    """
    if isinstance(raw, dict):
        raw = raw.get("rendered", "")
    text = _TAG_RE.sub("", str(raw or ""))
    return " ".join(html.unescape(text).split())


def _fetch_term_names(client: httpx.Client, endpoint: str) -> dict[int, str]:
    """`{term_id: name}` for a WP taxonomy endpoint (`categories` / `tags`).

    Posts reference terms by ID, but every consumer of this cache -- and the
    focus-category matching in `lib.content_strategy` -- works in names, so
    the mapping has to happen here rather than at each reader.
    """
    names: dict[int, str] = {}
    page = 1
    while True:
        resp = client.get(
            f"/wp-json/wp/v2/{endpoint}",
            params={"per_page": _PER_PAGE, "page": page, "_fields": "id,name"},
        )
        if resp.status_code != 200:
            logger.warning(
                "site_cache_terms_fetch_failed", endpoint=endpoint, status=resp.status_code
            )
            break
        batch = resp.json()
        if not isinstance(batch, list) or not batch:
            break
        for term in batch:
            if isinstance(term, dict) and term.get("id") is not None:
                # Decoded, not raw: WP returns term names HTML-escaped
                # ("Food &amp; Diet"). Storing the escaped form would mean a
                # focus category an operator types as "Food & Diet" never
                # compares equal to it, and the whole ranking would silently
                # do nothing.
                names[int(term["id"])] = _plain_text(term.get("name"))
        if len(batch) < _PER_PAGE:
            break
        page += 1
    return names


def _post_entry(
    post: dict[str, Any], *, categories: dict[int, str], tags: dict[int, str]
) -> dict[str, Any] | None:
    """One cache row, or None when the post lacks a title or URL.

    Skipped rather than raised on: one malformed post must not cost the whole
    refresh, which would leave the previous (older) cache in place.
    """
    title = _plain_text(post.get("title"))
    url = str(post.get("link") or "").strip()
    if not title or not url:
        return None

    def _names(key: str, lookup: dict[int, str]) -> list[str]:
        ids = post.get(key)
        if not isinstance(ids, list):
            return []
        return [lookup[int(i)] for i in ids if isinstance(i, int) and lookup.get(int(i))]

    return {
        "title": title,
        "url": url,
        "published_date": str(post.get("date_gmt") or ""),
        "categories": _names("categories", categories),
        "tags": _names("tags", tags),
        "excerpt": _plain_text(post.get("excerpt"))[:_EXCERPT_MAX_CHARS],
    }


def fetch_recent_posts(client: httpx.Client, *, max_posts: int) -> list[dict[str, Any]]:
    """Published posts newest-first, capped at `max_posts`.

    Newest-first is the WP default and is load-bearing downstream:
    `lib.crew.writer.context.rank_link_candidates` sorts stably, so this
    recency order survives as the tiebreak inside each category group.
    """
    if max_posts <= 0:
        return []
    categories = _fetch_term_names(client, "categories")
    tags = _fetch_term_names(client, "tags")

    entries: list[dict[str, Any]] = []
    page = 1
    while len(entries) < max_posts:
        resp = client.get(
            "/wp-json/wp/v2/posts",
            params={
                "per_page": min(_PER_PAGE, max_posts - len(entries)),
                "page": page,
                "status": "publish",
                "_fields": _POST_FIELDS,
            },
        )
        if resp.status_code != 200:
            logger.warning("site_cache_posts_fetch_failed", page=page, status=resp.status_code)
            break
        batch = resp.json()
        if not isinstance(batch, list) or not batch:
            break
        for post in batch:
            if not isinstance(post, dict):
                continue
            entry = _post_entry(post, categories=categories, tags=tags)
            if entry is not None:
                entries.append(entry)
        if len(batch) < _PER_PAGE:
            break
        page += 1

    return entries[:max_posts]


def build_cache(
    existing: dict[str, Any],
    posts: list[dict[str, Any]],
    *,
    site_url: str,
    site_name: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The full cache document. Pure -- the caller owns fetching and writing.

    `content_summary` is carried over from `existing` rather than rebuilt: a
    crawl cannot regenerate the `content_gaps` analysis `lib.keyword_research`
    reads from it, so dropping it here would quietly break that consumer.
    """
    stamp = (now or datetime.now(UTC)).isoformat()
    cache: dict[str, Any] = {
        "cached_at": stamp,
        "site_url": site_url,
        "site_name": site_name,
        "recent_posts": posts,
    }
    summary = existing.get("content_summary")
    if isinstance(summary, dict) and summary:
        cache["content_summary"] = summary
    return cache


def cache_path(brand_dir: Path) -> Path:
    """Current cache location (the 2026-06 reorg moved it under `data/cache/`)."""
    return brand_dir / "data" / "cache" / "site_content_cache.json"


def write_cache(brand_dir: Path, cache: dict[str, Any]) -> Path:
    """Atomic write, creating `data/cache/` if a fresh brand lacks it."""
    path = cache_path(brand_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, cache)
    return path
