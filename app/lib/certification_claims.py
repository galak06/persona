"""Guard against certification claims about products nobody verified.

"VOHC-accepted" is a factual claim about a specific product appearing on a
specific published list, not a matter of tone -- and a dental post invites it
on every product it names. Two live drafts made claims the list does not
support: one called Pet Honesty Dental Wipes "VOHC-accepted" (the brand is not
on the list at all), another attributed acceptance to Virbac's C.E.T.
Enzymatic chews when the accepted Virbac products are VEGGIEDENT. The
medical-claims gate passed both at 88 and 92 -- correctly, since nothing there
checks certifications.

Anchored on the AFFILIATE LINK, not on the prose. Matching product names in
text was tried first and does not work: Title Case headings are
indistinguishable from brand names, so it either flags "What VOHC Acceptance
Really Means" or misses a claim whose product is named after it. Every claim
that matters sits beside a link to the product it is about, and that link
carries an ASIN -- an exact identifier, no guessing.

The rule: a certification may be claimed beside a product link only when the
catalog records that product as verified. It cannot confirm anything against a
live registry; an operator verifies once and writes it into the catalog note.
General writing about what a seal means names no product, links to none, and
is never flagged.
"""

from __future__ import annotations

import re

from lib.medical_claims_validator import _is_negated

#: Marks in a catalog note that an operator checked a live registry. Written
#: by whoever verified it; absence means "not verified", never "not accepted".
VERIFIED_MARKER = "verified against"

#: Certification bodies whose acceptance is a checkable, published fact.
#: Extend per brand niche -- the mechanism is not dental-specific.
CERTIFICATIONS: tuple[str, ...] = ("VOHC", "AAFCO", "NASC")

_CLAIM_RE = re.compile(
    r"\b(?:" + "|".join(CERTIFICATIONS) + r")\b[\s–—-]*"
    r"(?:accepted|approved|certified|seal|listed|registered)",
    re.IGNORECASE,
)

_ASIN_RE = re.compile(r"/dp/([A-Z0-9]{10})")

#: How far from a product link a claim still counts as being ABOUT it. A
#: comparison-table row is the realistic unit; wider drags in the next row.
CONTEXT_CHARS = 400


def verified_asins(catalog: list[dict[str, object]]) -> set[str]:
    """ASINs whose catalog note records a real registry check."""
    return {
        str(entry.get("asin", "")).strip().upper()
        for entry in catalog
        if isinstance(entry, dict)
        and VERIFIED_MARKER in str(entry.get("notes") or "").lower()
        and entry.get("asin")
    }


def catalog_display(catalog: list[dict[str, object]], asin: str) -> str:
    """The catalog's name for `asin`, or the ASIN itself when unknown."""
    for entry in catalog:
        if isinstance(entry, dict) and str(entry.get("asin", "")).upper() == asin:
            return str(entry.get("display") or asin)
    return asin


def unverified_certification_claims(
    html: str, catalog: list[dict[str, object]]
) -> list[str]:
    """`"<product>: <claim excerpt>"` for every claim beside an unverified link.

    A claim counts as being about a product when it falls within
    `CONTEXT_CHARS` of that product's affiliate link -- close enough to be the
    same table row or sentence. Claims with no product link near them are
    general writing about the certification and are deliberately ignored.
    """
    if not html:
        return []
    verified = verified_asins(catalog)

    offenders: list[str] = []
    seen: set[str] = set()
    for link in _ASIN_RE.finditer(html):
        asin = link.group(1).upper()
        if asin in verified or asin in seen:
            continue
        window = html[max(0, link.start() - CONTEXT_CHARS) : link.end() + CONTEXT_CHARS]
        lowered = window.lower()
        # "these are NOT VOHC-accepted" is the honest sentence this gate exists
        # to make possible. Flagging it would punish a writer for being
        # accurate and teach the pipeline to stay vague instead. Reuses the
        # medical gate's clause-local negation test rather than growing a
        # second dialect of the same rule.
        claim = next(
            (m for m in _CLAIM_RE.finditer(window) if not _is_negated(lowered, m.start())),
            None,
        )
        if claim is None:
            continue
        seen.add(asin)
        excerpt = " ".join(window[max(0, claim.start() - 90) : claim.end()].split())
        offenders.append(f"{catalog_display(catalog, asin)}: ...{excerpt}")
    return offenders


def validate_certification_claims(
    html: str, catalog: list[dict[str, object]], *, title: str = ""
) -> None:
    """Hard-fail gate: raise ValueError on a claim beside an unverified product.

    Call on the assembled HTML -- after affiliate placeholders resolve, since
    the links are the anchor -- alongside
    `lib.medical_claims_validator.validate_blog_post`. Fails closed for the
    same reason: a false compliance claim must never reach a draft an operator
    might publish without re-reading.
    """
    offenders = unverified_certification_claims(html, catalog)
    if offenders:
        listed = "; ".join(offenders[:5])
        raise ValueError(
            f"{len(offenders)} certification claim(s) beside products not recorded as "
            f"verified{' in ' + title if title else ''}: {listed}"
        )
