"""Internal-link enforcement — the "never let the LLM invent a URL" layer.

Split out of `lib.crew.writer.context` (file-size discipline) because it is a
distinct responsibility from BUILDING link candidates: context decides what
the strategist may choose from, this module polices what actually came back.

Three overlapping guards, deliberately not one:
  * `sanitize_internal_links` -- the brief's structured candidate list
  * `filter_links_to_allowed` -- the writer's self-reported links-used list
  * `strip_unapproved_internal_links` -- anchors embedded in the body HTML
    that were never declared in either list at all

Each covers a different way an invented URL reaches the page, and the last is
the only one that inspects the rendered HTML.
"""

from __future__ import annotations

import re

from lib.crew.writer.models import ContentBrief, InternalLinkCandidate
from lib.observability import get_logger

logger = get_logger(__name__)


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
