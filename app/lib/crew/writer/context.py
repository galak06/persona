"""Compact context-gathering + defensive sanitizing for the strategist/writer pipeline.

Same "summarize, don't dump" posture as `lib.crew.context` (imported and
reused there for brand identity/voice -- see `lib.crew.writer.orchestrator`).
This module adds the pieces specific to drafting a full post: mascot-fact
grounding (so the writer never fabricates a specific about the brand's real
mascot), real internal-link candidates from the site content cache, and the
brand's real affiliate product catalog.

`sanitize_internal_links` is the actual enforcement of "never let the LLM
invent a URL" -- prompt instructions alone are not a guarantee, so any
strategist-proposed link whose URL isn't in the real candidate set is
dropped here, defensively, the same "don't fully trust the model" posture
`lib.affiliate_resolver.resolve_html` takes with unknown catalog keys.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from lib.affiliate_resolver import ProductEntry
from lib.crew.writer.models import ContentBrief, InternalLinkCandidate
from lib.observability import get_logger

logger = get_logger(__name__)

_NALLA_FACTS_MAX_CHARS = 1500
_CURRENT_NALLA_FACTS = Path("data") / "config" / "nalla_facts.md"

_CURRENT_AFFILIATE_CATALOG = Path("data") / "config" / "affiliate_products.json"
_CURRENT_CONTENT_RULES = Path("data") / "config" / "content_rules.json"

_DEFAULT_DISCLOSURE_TEXT = (
    "Affiliate disclosure: This post contains Amazon affiliate links. If you buy "
    "through them, we may earn a small commission at no extra cost to you."
)


def read_brand_config(brand_dir: Path) -> dict[str, Any]:
    """Small local reader for `config.json`, tolerant of a missing/malformed
    file (never raises) -- same pattern `lib.crew.scout._read_config` already
    uses, kept as an independent copy rather than a shared import (see
    `lib.crew.writer.agent`'s module docstring on this pipeline's
    "independently-evolving" posture toward the scout's own files)."""
    path = brand_dir / "config.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("crew_writer_config_json_parse_failed", path=str(path))
        return {}
    return data if isinstance(data, dict) else {}


def idea_priority(row: dict[str, Any]) -> float:
    """Score used to rank `status='publish'` ideas -- crewai_scout rows carry
    `priority_score` in `input`, gsc_scout rows carry `score`; either is
    treated as "how strong is this opportunity", 0.0 if neither parses."""
    raw_input = row.get("input")
    if not raw_input:
        return 0.0
    try:
        payload = json.loads(raw_input) if isinstance(raw_input, str) else raw_input
    except json.JSONDecodeError:
        return 0.0
    if not isinstance(payload, dict):
        return 0.0
    for key in ("priority_score", "score"):
        if key in payload:
            try:
                return float(payload[key])
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def mascot_facts_summary(brand_dir: Path, *, max_chars: int = _NALLA_FACTS_MAX_CHARS) -> str:
    """Truncated excerpt of the brand's mascot-facts grounding file (e.g.
    `nalla_facts.md`) -- the "only TRUE things the mascot may be said to do"
    guardrail. Returns "" if the file doesn't exist, so callers can drop the
    section rather than send an empty header (mirrors
    `lib.crew.context.brand_voice_summary`'s tolerant-missing-file contract).
    """
    path = brand_dir / _CURRENT_NALLA_FACTS
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit("\n", 1)[0] + "\n...(truncated)"


def internal_link_candidates_from_cache(
    site_cache: dict[str, Any],
) -> list[InternalLinkCandidate]:
    """Real internal-link candidates from `site_content_cache.json`'s
    `recent_posts` (via `lib.gsc_scout.load_site_content_cache`, read by the
    caller -- this function is pure). Skips any entry missing a title or url
    rather than raising on a malformed cache row."""
    posts = site_cache.get("recent_posts", []) if isinstance(site_cache, dict) else []
    candidates: list[InternalLinkCandidate] = []
    for post in posts or []:
        if not isinstance(post, dict):
            continue
        title = str(post.get("title") or "").strip()
        url = str(post.get("url") or "").strip()
        if title and url:
            candidates.append(InternalLinkCandidate(title=title, url=url))
    return candidates


def internal_link_candidates_json(candidates: list[InternalLinkCandidate]) -> str:
    """Compact JSON of real internal-link candidates for the strategist prompt."""
    return json.dumps([c.model_dump() for c in candidates], ensure_ascii=False)


def sanitize_internal_links(brief: ContentBrief, allowed_urls: set[str]) -> ContentBrief:
    """Drop any `internal_link_candidates` entry whose URL isn't in the real
    candidate set given to the strategist -- defense against the LLM
    inventing a plausible-looking URL despite prompt instructions."""
    kept = [c for c in brief.internal_link_candidates if c.url in allowed_urls]
    dropped = len(brief.internal_link_candidates) - len(kept)
    if dropped:
        logger.warning("crew_writer_dropped_invented_internal_links", count=dropped)
    return brief.model_copy(update={"internal_link_candidates": kept})


def filter_links_to_allowed(
    links: list[InternalLinkCandidate], allowed_urls: set[str]
) -> list[InternalLinkCandidate]:
    """Same filter as `sanitize_internal_links`, applied to a bare link list
    (used on the writer's self-reported `internal_links_used`, which is a
    second, independent LLM call and gets the same defensive treatment)."""
    kept = [link for link in links if link.url in allowed_urls]
    dropped = len(links) - len(kept)
    if dropped:
        logger.warning("crew_writer_dropped_invented_links_used", count=dropped)
    return kept


_ANCHOR_RE = re.compile(r'<a\s+[^>]*href="([^"]*)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)


def strip_unapproved_internal_links(
    body_html: str, *, site_url: str, allowed_urls: set[str]
) -> str:
    """Defang any same-site `<a href>` in the WRITTEN HTML BODY whose URL isn't
    in the real candidate set -- `sanitize_internal_links`/`filter_links_to_allowed`
    only police the brief's/post's structured link fields, but the writer is
    free-form prose and can (live-confirmed: did, once, in this build's own
    validation run) embed an invented internal link directly in running text
    without declaring it in `internal_links_used` at all. Same "don't fully
    trust the model" posture, applied at the one place the URL actually ships.

    Only touches links that look like THIS brand's own site (prefix-matches
    `site_url`) -- external links (affiliate URLs, citations) are untouched,
    since only internal links are claimed to come from the real candidate set.
    Un-approved matches are defanged to plain text (anchor tag dropped, the
    visible link text is kept) rather than deleting the sentence around them.
    """
    site_prefix = site_url.rstrip("/") + "/"

    def _replace(match: re.Match[str]) -> str:
        url, text = match.group(1), match.group(2)
        if url.startswith(site_prefix) and url not in allowed_urls:
            logger.warning("crew_writer_stripped_invented_body_link", url=url)
            return text
        return match.group(0)

    return _ANCHOR_RE.sub(_replace, body_html)


#: `(?:(?!</?p\b).)*?` -- "any character, as long as it isn't the start of a
#: `<p>`/`</p>` tag" -- keeps the lazy before/after groups from spanning past
#: an unrelated paragraph boundary (a naive `.*?` here matched clean across
#: `<p>A</p><ul>...</ul><p>B</p>`, corrupting already-correctly-nested HTML).
_BLOCK_IN_PARAGRAPH_RE = re.compile(
    r"<p>((?:(?!</?p\b).)*?)(<(?:ul|ol)>.*?</(?:ul|ol)>)((?:(?!</?p\b).)*?)</p>", re.DOTALL
)


def unwrap_lists_from_paragraphs(body_html: str) -> str:
    """Fix `<p>...<ul>...</ul>...</p>` -- invalid HTML5 (a `<ul>`/`<ol>` is
    block content, not permitted inside a `<p>`, which only permits phrasing
    content). The writer LLM sometimes emits exactly this shape (live-
    reproduced: "Related reading:<br><ul>...</ul>" wrapped in a single `<p>`,
    on a real generated post). Browsers/WordPress auto-close the `<p>` at the
    list boundary, and the leftover orphan `</p>` corrupts WordPress's
    rendering into a stray, unbalanced `</div>` that collapses the theme's
    page layout (narrow content column, huge blank gap, sidebar flung right
    -- reproduced live on post 4147, a DIFFERENT root cause from the
    JSON-LD-nesting bug `lib.crew.draft._build_wrapped_body` guards against).

    Splits the list out of the paragraph into its own sibling block, leaving
    any real text before/after the list as its own `<p>` (dropped entirely
    if empty/whitespace-only, so a paragraph that's ONLY a list doesn't leave
    a stray empty `<p></p>` behind).
    """

    def _fix(match: re.Match[str]) -> str:
        before, list_html, after = match.group(1).strip(), match.group(2), match.group(3).strip()
        parts = [f"<p>{before}</p>"] if before else []
        parts.append(list_html)
        if after:
            parts.append(f"<p>{after}</p>")
        return "".join(parts)

    return _BLOCK_IN_PARAGRAPH_RE.sub(_fix, body_html)


def load_brand_affiliate_catalog(brand_dir: Path) -> dict[str, ProductEntry]:
    """Load the BRAND's real affiliate product catalog (not the engine-root
    default `lib.affiliate_resolver._load_catalog()` points at -- this repo's
    real catalog lives per-brand, under `data/config/affiliate_products.json`).

    Returns `{}` if the file is missing -- callers treat an empty catalog as
    "no products for this brand yet", not an error; `resolve_html` will still
    correctly refuse any `[AFFILIATE:key]` placeholder against an empty dict.
    """
    path = brand_dir / _CURRENT_AFFILIATE_CATALOG
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("crew_writer_affiliate_catalog_json_parse_failed", path=str(path))
        return {}
    catalog: dict[str, ProductEntry] = {}
    for entry in raw:
        key = str(entry.get("key", "")).strip().lower()
        asin = str(entry.get("asin", "")).strip()
        if not key or not asin:
            logger.warning(
                "crew_writer_affiliate_catalog_entry_missing_key_or_asin",
                path=str(path),
                entry_key=key or None,
            )
            continue
        catalog[key] = ProductEntry(
            key=key,
            asin=asin,
            display=entry.get("display", key),
            category=entry.get("category"),
            notes=entry.get("notes"),
        )
    return catalog


def catalog_summary_text(catalog: dict[str, ProductEntry]) -> str:
    """Human-readable "key: display (category) -- notes" lines for the writer
    prompt, so the writer only ever references real `[AFFILIATE:key]` keys.
    """
    if not catalog:
        return (
            "(no product catalog entries for this brand -- omit the product "
            "comparison table, the 'Our Pick' affiliate link, and any "
            "[AFFILIATE:key] placeholders entirely)"
        )
    lines = []
    for entry in catalog.values():
        suffix = f" -- {entry.notes}" if entry.notes else ""
        category = f" ({entry.category})" if entry.category else ""
        lines.append(f"{entry.key}: {entry.display}{category}{suffix}")
    return "\n".join(lines)


def load_disclosure_text(brand_dir: Path) -> str:
    """The brand's real, preferred FTC disclosure sentence from
    `content_rules.json`'s `affiliate.disclosure_text`, falling back to a
    generic-but-compliant default (still matches
    `lib.affiliate_resolver._DISCLOSURE_MARKERS`'s "affiliate disclosure"
    marker) if the brand hasn't specified one.
    """
    path = brand_dir / _CURRENT_CONTENT_RULES
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("crew_writer_content_rules_json_parse_failed", path=str(path))
            data = {}
        text = str((data.get("affiliate") or {}).get("disclosure_text") or "").strip()
        if text:
            return text
    return _DEFAULT_DISCLOSURE_TEXT
