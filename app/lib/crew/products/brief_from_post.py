"""Rebuild a `ContentBrief` from an ALREADY-PUBLISHED WordPress post.

The product selector (`lib.crew.products.agent`) judges a post from the
strategist's plan, not from finished HTML. Posts published before that
stage existed have no persisted `ContentBrief`, so the one-time backfill
(`scripts/backfill_blog_product_blocks.py`) reconstructs an equivalent one
from what WordPress still has: the title, the slug, and the `<h2>`
structure of the body.

Only the fields `lib.crew.products.prompts.build_selector_task_description`
actually reads are reconstructed faithfully (title, primary keyword,
outline). `mascot_angle` is required by the model but never read by that
prompt, so it gets an honest fixed placeholder rather than an invented
brand angle.
"""

from __future__ import annotations

import html as _html
import re
from typing import Final

from lib.crew.writer.models import ContentBrief, OutlineSection

#: Enough of each section for the selector to judge what it covers; the
#: prompt lists one line per section, so longer notes just add noise.
_MAX_NOTES_CHARS: Final[int] = 200

#: `mascot_angle` is a required (non-defaulted) `ContentBrief` field but is
#: not referenced by the selector prompt -- say so instead of faking one.
BACKFILL_MASCOT_ANGLE: Final[str] = (
    "Not applicable: reconstructed brief for an already-published post "
    "(product selection only; the selector prompt does not read this field)."
)

_H2_RE: Final[re.Pattern[str]] = re.compile(r"<h2\b[^>]*>(.*?)</h2>", re.IGNORECASE | re.DOTALL)
_TAG_RE: Final[re.Pattern[str]] = re.compile(r"<[^>]+>")
_WS_RE: Final[re.Pattern[str]] = re.compile(r"\s+")


def strip_html(fragment: str) -> str:
    """HTML fragment -> collapsed plain text (tags dropped, entities decoded)."""
    return _WS_RE.sub(" ", _html.unescape(_TAG_RE.sub(" ", fragment))).strip()


def _truncate(text: str, limit: int = _MAX_NOTES_CHARS) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def outline_from_html(content_html: str) -> list[OutlineSection]:
    """One `OutlineSection` per `<h2>`, with `notes` = the start of the text
    that follows it (up to the next `<h2>`).

    Both `heading` and `notes` are required fields on `OutlineSection`, so a
    heading with no following prose still gets a non-empty placeholder.
    """
    sections: list[OutlineSection] = []
    matches = list(_H2_RE.finditer(content_html))
    for index, match in enumerate(matches):
        heading = strip_html(match.group(1))
        if not heading:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content_html)
        body = strip_html(content_html[match.end() : end])
        sections.append(
            OutlineSection(
                heading=heading,
                level="H2",
                notes=_truncate(body) or "(no body text captured for this section)",
            )
        )
    return sections


def keyword_from_slug(slug: str) -> str:
    """`canned-pumpkin-dogs-guide` -> `canned pumpkin dogs guide`."""
    return _WS_RE.sub(" ", slug.replace("-", " ").replace("_", " ")).strip()


def synthesize_brief(*, title: str, slug: str, content_html: str) -> ContentBrief:
    """The reconstructed brief for one published post.

    `title` may be WP's rendered title (entity-encoded, possibly with
    markup) -- it is unescaped and de-tagged here.
    """
    clean_title = strip_html(title) or keyword_from_slug(slug)
    return ContentBrief(
        suggested_title=clean_title,
        outline=outline_from_html(content_html),
        primary_keyword=keyword_from_slug(slug) or clean_title,
        mascot_angle=BACKFILL_MASCOT_ANGLE,
    )
