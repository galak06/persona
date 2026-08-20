"""Guarantee the FTC / Amazon-Associates surface on a finished post body.

Every `amazon.com/dp/<ASIN>` link a post ships with has to carry
`rel="sponsored nofollow"` (Google's paid-link requirement, and Amazon's),
`target="_blank"`, and an `ascsubtag` campaign id -- without which Amazon
reporting cannot attribute a click to the post that earned it -- and the
post has to carry the *specific* "As an Amazon Associate..." statement the
Operating Agreement asks for, not merely the generic word "affiliate".

Until this module existed all four came from ONE place,
`lib.crew.products.block.render_blog_block`, so they were only ever present
when the picks block happened to be appended. The moment the writer linked a
catalog product inline, `ensure_product_block` took its "the body already
recommends one, leave it alone" branch and the entire compliance surface
silently left with the block. Fourteen live posts shipped that way between
2026-08-14 and 2026-08-19, four of them published.

So the surface stops riding on the block. `enforce_affiliate_compliance` is
a pure, IDEMPOTENT post-process over finished HTML: running it twice equals
running it once, which is what lets the drafting path and a one-off repair
of an already-published post share ONE implementation without either
needing to know what the other did.

Two deliberate non-actions:

  * Only Amazon product links are touched. Every rewrite is keyed on
    `amazon.com/dp/` inside the `href`, so a same-site internal link can
    never be rewritten.
  * A body with no Amazon link at all comes back byte-for-byte unchanged,
    disclosure included -- a disclosure for links that do not exist is
    noise, not compliance (live: post 4482 carries exactly that).
"""

from __future__ import annotations

import html as _html
import re
from typing import Final

from lib.affiliate_resolver import _has_disclosure
from lib.crew.products.block import DISCLOSURE, blog_campaign_id
from lib.observability import get_logger

logger = get_logger(__name__)

#: The href substring that marks a link as an Amazon PRODUCT link, and the
#: single thing every rewrite here keys on.
AMAZON_PRODUCT_HREF: Final[str] = "amazon.com/dp/"

#: Exactly what `render_blog_block` emits, so a body fixed here and a body
#: carrying a rendered picks block are indistinguishable.
REL_VALUE: Final[str] = "sponsored nofollow"
TARGET_VALUE: Final[str] = "_blank"
DISCLOSURE_HTML: Final[str] = f"<p><em>{_html.escape(DISCLOSURE)}</em></p>"

_SLUG_STRIP_RE: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9]+")

_ANCHOR_OPEN_RE: Final[re.Pattern[str]] = re.compile(r"<a\b[^>]*>", re.IGNORECASE)
_HREF_RE: Final[re.Pattern[str]] = re.compile(
    r"""href\s*=\s*(?P<quote>["'])(?P<url>.*?)(?P=quote)""", re.IGNORECASE | re.DOTALL
)
_REL_ATTR_RE: Final[re.Pattern[str]] = re.compile(r"\brel\s*=", re.IGNORECASE)
_TARGET_ATTR_RE: Final[re.Pattern[str]] = re.compile(r"\btarget\s*=", re.IGNORECASE)

#: Spans a bare-URL wrap must never reach into: an existing anchor (its href
#: AND its visible text), a `<script>` body (the JSON-LD tail
#: `lib.crew.writer.assemble` appends), and any other tag's attributes.
_PROTECTED_RE: Final[re.Pattern[str]] = re.compile(
    r"<a\b[^>]*>.*?</a>|<script\b[^>]*>.*?</script>|<[^>]+>",
    re.DOTALL | re.IGNORECASE,
)

#: A bare Amazon URL typed into prose. The tail is a WHITELIST of URL-legal
#: characters that must END on a non-punctuation one, so a URL run straight
#: into an em-dash or a full stop stops at the URL. Live case: post 4466 had
#: three affiliate URLs sitting in prose as plain text -- WordPress's
#: `make_clickable` does not linkify those either, so they earned nothing.
_BARE_URL_RE: Final[re.Pattern[str]] = re.compile(
    r"https?://(?:www\.)?amazon\.com/dp/[A-Z0-9]{10}"
    r"(?:[A-Za-z0-9\-._~:/?#@!$&*+,;=%]*[A-Za-z0-9\-_~/#@$*+=%])?"
)

_PARAGRAPH_RE: Final[re.Pattern[str]] = re.compile(r"<p\b[^>]*>.*?</p>", re.DOTALL | re.IGNORECASE)


def slug_for(title: str) -> str:
    """A WP-style slug from a post TITLE, for the `ascsubtag` campaign id.

    Derived from the title and deliberately never from a WordPress `slug`
    field: WP returns `slug: ""` for an unpublished draft, and the drafting
    crew only ever creates drafts. Trusting it baked a dead
    `ascsubtag=blog-` into two live posts (4289, 4295).
    """
    return _SLUG_STRIP_RE.sub("-", title.lower()).strip("-")[:60].strip("-") or "post"


def _with_campaign(url: str, campaign_id: str) -> str:
    """`url` carrying `ascsubtag=<campaign_id>`, appended once.

    `&amp;` (not a raw `&`) when a query string already exists, matching
    `render_blog_block`'s `html.escape`d output byte-for-byte; `?` when
    there is none; and always BEFORE any `#fragment`, which would otherwise
    swallow the parameter.
    """
    if "ascsubtag=" in url:
        return url
    base, hash_sep, fragment = url.partition("#")
    separator = "&amp;" if "?" in base else "?"
    return f"{base}{separator}ascsubtag={campaign_id}{hash_sep}{fragment}"


def _compliant_anchor(tag: str, campaign_id: str) -> str:
    """One `<a ...>` open tag plus whatever of the campaign id / `rel` /
    `target` it is missing. Attributes already present are left exactly as
    written -- no duplicate `rel`, and no second pass ever changes anything.
    """
    href = _HREF_RE.search(tag)
    if href is None or AMAZON_PRODUCT_HREF not in href.group("url"):
        return tag
    url = _with_campaign(href.group("url"), campaign_id)
    if url != href.group("url"):
        tag = f"{tag[: href.start('url')]}{url}{tag[href.end('url') :]}"
    # `rel=`/`target=` are looked for with the href REMOVED, so a URL that
    # happens to carry a `rel=` query parameter cannot suppress the real one.
    attributes = _HREF_RE.sub("", tag)
    additions = ""
    if not _REL_ATTR_RE.search(attributes):
        additions += f' rel="{REL_VALUE}"'
    if not _TARGET_ATTR_RE.search(attributes):
        additions += f' target="{TARGET_VALUE}"'
    if not additions:
        return tag
    return f"{tag[:-1].rstrip()}{additions}>"


def _wrap_bare_url(match: re.Match[str]) -> str:
    """A bare URL -> a plain anchor over the SAME visible text. The attribute
    pass that runs next gives it the campaign id, `rel` and `target`, so
    there is only one place those are spelled out."""
    return f'<a href="{match.group(0)}">{match.group(0)}</a>'


def _wrap_bare_urls(html: str) -> str:
    """Link Amazon URLs sitting in prose, and only in prose: everything
    inside an existing anchor, a `<script>`, or any tag's attributes is
    copied through untouched, which is also what makes this idempotent (a
    URL wrapped by the first pass is inside an anchor by the second)."""
    out: list[str] = []
    cursor = 0
    for protected in _PROTECTED_RE.finditer(html):
        out.append(_BARE_URL_RE.sub(_wrap_bare_url, html[cursor : protected.start()]))
        out.append(protected.group(0))
        cursor = protected.end()
    out.append(_BARE_URL_RE.sub(_wrap_bare_url, html[cursor:]))
    return "".join(out)


def _ensure_disclosure(html: str) -> str:
    """The Associates statement, present exactly once.

    A generic "Affiliate disclosure: Amazon affiliate links" line already
    satisfies `lib.affiliate_resolver._has_disclosure` -- which is precisely
    why the picks block's disclosure got suppressed on every affected post --
    so the presence test here is the SPECIFIC wording, not that token set.
    Placement is next to whatever disclosure the post already carries; the
    `<script>` fallback keeps it out of the JSON-LD tail, which
    `lib.crew.draft_body.split_body_and_jsonld` treats as schema, never body.
    """
    if DISCLOSURE.lower() in html.lower():
        return html
    for paragraph in _PARAGRAPH_RE.finditer(html):
        if _has_disclosure(paragraph.group(0)):
            end = paragraph.end()
            return f"{html[:end]}\n{DISCLOSURE_HTML}{html[end:]}"
    script = html.lower().find("<script")
    if script != -1:
        return f"{html[:script]}{DISCLOSURE_HTML}\n\n{html[script:]}"
    return f"{html.rstrip()}\n{DISCLOSURE_HTML}\n"


def enforce_affiliate_compliance(html: str, slug: str, *, link_bare_urls: bool = True) -> str:
    """`html` with every Amazon product link made FTC/Associates-compliant.

    Idempotent and total: safe to run over a body that is already correct,
    already carries a picks block, or carries no Amazon link at all.

    Args:
        html: Finished post HTML. `[AFFILIATE:key]` placeholders are NOT
            resolved here -- run this after
            `lib.affiliate_resolver.resolve_html`, or the links it would
            create are still placeholders and nothing matches.
        slug: The post's slug, WITHOUT the `blog-` campaign prefix (see
            `slug_for`, and `lib.crew.products.block.blog_campaign_id`).
        link_bare_urls: Wrap Amazon URLs typed into prose as plain text into
            real anchors first. On by default: an unlinked affiliate URL is
            both non-compliant and worthless. Pass `False` to restrict the
            pass to markup that is already anchored.

    Returns:
        The compliant HTML -- the same object's value unchanged when there
        was nothing to do.
    """
    if AMAZON_PRODUCT_HREF not in html:
        return html

    campaign_id = blog_campaign_id(slug)
    before = html
    if link_bare_urls:
        html = _wrap_bare_urls(html)
    html = _ANCHOR_OPEN_RE.sub(lambda m: _compliant_anchor(m.group(0), campaign_id), html)
    html = _ensure_disclosure(html)

    if html != before:
        logger.info(
            "affiliate_compliance_enforced",
            campaign_id=campaign_id,
            amazon_links=html.count(AMAZON_PRODUCT_HREF),
            chars_added=len(html) - len(before),
        )
    return html
