"""Affiliate-compliance rules on a spotlight's ``SocialPostPlan``.

CONTRACT FROZEN BY PHASE 0 -- the flag constants are REAL and final (the
compose service and the review card key off them); the two function signatures
and the rules their docstrings enumerate are final. Phase 1C replaces the
``NotImplementedError`` bodies and changes nothing else.

Spotlights only. ``lib.crew.socialpost.rules.find_caption_violations`` still
runs first and unchanged (keyword coverage, no links, hashtag count, comment
keyword, closing question); these are ADDED through the one new kwarg on
``execute_social_post_crew(..., extra_violations=...)``, so a regular post
never sees them and its behaviour cannot change.

Two tiers, because the two kinds of problem want opposite handling:

* ``find_spotlight_violations`` -- BLOCKING. Structural, mechanically
  checkable, and wrong rather than merely worse: an affiliate post without the
  disclosure, an unverified certification, a price. Fed back into the crew's
  retry loop verbatim, so each string must read as an instruction to the
  writer ("fb_caption must ...").
* ``find_spotlight_flags`` -- FLAG-ONLY. Judgement calls a regex cannot settle.
  They never fail a composition; they are stored in ``validation_flags`` and
  shown on the review card for the human who approves every spotlight.

Both are pure: a plan and a product in, a list of strings out. No LLM, no I/O.
"""

from __future__ import annotations

import re
from typing import Final

from lib.affiliate_resolver import ProductEntry
from lib.crew.socialpost.models import SocialPostPlan
from lib.crew.spotlight.cert_gate import claimed_certifications
from lib.crew.spotlight.product_terms import (
    brand_token,
    brand_token_re,
    display_name,
    mentions_brand,
    verified_certifications,
)
from lib.crew.spotlight.prompts import DISCLOSURE_MARKER, DISCLOSURE_SENTENCE

#: The chosen product is not linked in the live post. Added by
#: ``compose_spotlight`` (not by ``find_spotlight_flags`` -- it is a fact about
#: the post, not about the plan). Never a failure: the owner may spotlight any
#: catalog product; the card just says the article does not feature it.
FLAG_PRODUCT_NOT_IN_POST: Final[str] = "product_not_in_post"

#: A sentence says the mascot eats/uses/loves the product and the facts file
#: does not mention the product.
FLAG_MASCOT_USAGE_UNVERIFIED: Final[str] = "mascot_usage_unverified"

#: Where each caption is told to put the disclosure, so the retry feedback
#: repeats the prompt's own placement rule instead of a second, vaguer one.
_DISCLOSURE_PLACEMENT: Final[dict[str, str]] = {
    "fb_caption": "on its own last line, AFTER the closing question",
    "ig_caption": "on its own line, just BEFORE the hashtag line",
}

# Nouns that make an image model paint a product surface. `brand text` is not
# listed: a brief saying "no brand text" would then fail its own instruction,
# and the brand half of rule 5 is the token check instead. Everything past
# `boxes` was added 2026-09-18: the prompt's own catch-all is "or any readable
# surface", and a pouch, a tub or a jar is exactly as paintable as a bag.
# Whole-word throughout, on purpose -- `tin` must not fire on "tiny", `tub` not
# on "tube", `jar` not on "jarring".
_PACKAGING_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:packaging|packages?|labels?|logos?|bags?|box|boxes|pouch(?:es)?"
    r"|tubs?|canisters?|jars?|packets?|wrappers?|tins?|cartons?|containers?)\b",
    re.IGNORECASE,
)

# A price the reader can check. Amazon's operating agreement forbids quoting
# one because it goes stale between composition and the reader seeing it.
#
# Widened 2026-09-18 to cover what rule 3's own message already promised and
# CLAUDE.md states as "Never state a price": "half price", "big discount",
# "Save 25% today", "cheaper than", and "$  12" (the gap was `\s?`, one space)
# all reached a live affiliate caption. `\bsale\b` cannot fire inside
# "wholesale" and `\bcheap` is a prefix so it also takes "cheaper"/"cheapest".
_MONEY_RE: Final[re.Pattern[str]] = re.compile(
    r"[$£€]\s*\d"
    r"|\b\d+(?:[.,]\d{1,2})?\s*(?:usd|eur|gbp|dollars?|euros?|pounds?)\b"
    r"|\bpric(?:e|es|ed|ing)\b"
    r"|\bcheap",
    re.IGNORECASE,
)
_DISCOUNT_RE: Final[re.Pattern[str]] = re.compile(
    r"%\s*off|\bdeals?\b|\bsale\b|\bdiscount(?:ed|s|ing)?\b|\bsave\s+\d",
    re.IGNORECASE,
)

# Verbs that turn "the mascot and the product in one sentence" into a claim
# about the mascot having actually had it.
_USAGE_VERB_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:eat|eats|ate|eaten|eating|use|uses|used|using|love|loves|loved|loving"
    r"|chew|chews|chewed|chewing|get|gets|got|try|tries|tried|trying)\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"(?<=[.!?])\s+|\n+")


def _captions(plan: SocialPostPlan) -> tuple[tuple[str, str], ...]:
    """The two fields a reader sees, labelled with the plan field name so a
    violation string names the field the writer has to edit."""
    return (("fb_caption", plan.fb_caption), ("ig_caption", plan.ig_caption))


def _overlay(plan: SocialPostPlan) -> tuple[tuple[str, str], ...]:
    """The text painted onto the image. Read as copy by anyone who sees the
    post, so the certification and price rules apply to it exactly as they do
    to the captions."""
    return (
        ("overlay_headline", plan.overlay_headline),
        ("overlay_subcopy", plan.overlay_subcopy),
    )


def _disclosure_violations(plan: SocialPostPlan) -> list[str]:
    """Rule 1 -- the disclosure marker, case-insensitively, in both captions."""
    return [
        f"{label} must contain the affiliate disclosure verbatim, "
        f'{_DISCLOSURE_PLACEMENT[label]}: "{DISCLOSURE_SENTENCE}"'
        for label, caption in _captions(plan)
        if DISCLOSURE_MARKER not in caption.lower()
    ]


def _certification_violations(plan: SocialPostPlan, product: ProductEntry) -> list[str]:
    """Rule 2 -- a registry may only be named when an operator checked THAT
    registry for THIS product. Acronym or spelled-out name, either counts;
    ``cert_gate`` has dropped the ADJACENTLY negated mentions already, because
    a caption saying "not VOHC accepted" is making the safe claim, not the
    risky one."""
    allowed = {token.upper() for token in verified_certifications(product)}
    permitted = (
        f"the only certification verified for this product is {', '.join(sorted(allowed))}"
        if allowed
        else "no certification has been verified for this product"
    )
    violations: list[str] = []
    for label, text in _captions(plan) + _overlay(plan):
        named = [body for body in claimed_certifications(text) if body not in allowed]
        if named:
            violations.append(
                f"{label} must not claim {' or '.join(named)} acceptance -- {permitted}. "
                "Remove the certification and describe the product without it."
            )
    return violations


def _price_violations(plan: SocialPostPlan) -> list[str]:
    """Rule 3 -- no price, no discount, anywhere a reader can see."""
    fields = _captions(plan) + _overlay(plan) + (("cta_ribbon_text", plan.cta_ribbon_text),)
    return [
        f"{label} must not mention a price, a discount, a deal or a sale -- "
        "affiliate copy may not quote prices that go stale. Say what the product "
        "does instead."
        for label, text in fields
        if _MONEY_RE.search(text) or _DISCOUNT_RE.search(text)
    ]


def _naming_violations(plan: SocialPostPlan, product: ProductEntry) -> list[str]:
    """Rule 4 -- an affiliate post whose captions never name the product sends
    readers to a DM asking "which one?"."""
    token = brand_token(product)
    if not token:
        return []
    return [
        f'{label} must name the product -- work "{display_name(product)}" '
        f'(at minimum the word "{token}") into it, not "this treat" or "the one I use".'
        for label, caption in _captions(plan)
        if not mentions_brand(caption, product)
    ]


def _image_brief_violations(plan: SocialPostPlan, product: ProductEntry) -> list[str]:
    """Rule 5 -- the scene only. An image model asked for a branded bag paints
    a plausible, entirely fake label onto a real product."""
    brief = plan.image_brief
    violations: list[str] = []
    found = sorted({m.group(0).lower() for m in _PACKAGING_RE.finditer(brief)})
    if found:
        violations.append(
            f"image_brief must describe the scene only -- remove "
            f"{', '.join(repr(w) for w in found)}. The image model invents a fake "
            "label on any packaging it is asked for."
        )
    if brand_token(product) and mentions_brand(brief, product):
        violations.append(
            f'image_brief must not name the brand ("{brand_token(product)}") -- '
            "describe the dog, the hands, the setting and the light instead."
        )
    return violations


def find_spotlight_violations(plan: SocialPostPlan, *, product: ProductEntry) -> list[str]:
    """Every BLOCKING rule the plan breaks; ``[]`` means usable.

    Bound with ``functools.partial(find_spotlight_violations, product=product)``
    and passed as ``extra_violations``. Rules, each yielding one human-readable
    instruction per offending field:

    1. ``prompts.DISCLOSURE_MARKER`` missing from ``fb_caption`` or
       ``ig_caption`` (case-insensitive).
    2. A NON-negated certification (``cert_gate.claimed_certifications``: every
       body in ``lib.certification_claims.CERTIFICATIONS`` by acronym OR by its
       spelled-out name, whole word, case-insensitive) in either caption,
       ``overlay_headline`` or ``overlay_subcopy`` that is not in
       ``product_terms.verified_certifications(product)``. So a verified VOHC
       product may say "VOHC-accepted"; an unverified one may not, and may not
       say "Veterinary Oral Health Council accepted" either; a verified VOHC
       product still may not say "AAFCO"; "not VOHC-accepted" passes. Negation
       must be ADJACENT -- see ``cert_gate`` for why this one fails closed.
    3. A currency amount (``$12``, ``12.99 USD``, ``£``/``€`` amounts), the
       word ``price``/``priced``/``pricing``, ``cheap`` and its comparatives,
       ``% off``, ``deal``, ``sale``, ``discount`` or ``save <number>`` (whole
       words, case-insensitive) in either caption, either overlay field or
       ``cta_ribbon_text`` -- Amazon's operating agreement forbids quoting
       prices that can go stale.
    4. ``product_terms.mentions_brand`` is false for ``fb_caption`` or for
       ``ig_caption``.
    5. ``image_brief`` mentions packaging -- any of ``packaging``, ``package``,
       ``label``, ``logo``, ``bag``, ``box``, ``pouch``, ``tub``, ``canister``,
       ``jar``, ``packet``, ``wrapper``, ``tin``, ``carton``, ``container`` as
       whole words -- or the brand token
       (``product_terms.mentions_brand``). An image model asked for a
       branded bag invents a label, and an invented label on a real product is
       a misrepresentation.

    Rules 4 and the brand half of 5 are SKIPPED when
    ``product_terms.brand_token(product)`` is ``""`` ("cannot check" is not
    "absent").
    """
    return [
        *_disclosure_violations(plan),
        *_certification_violations(plan, product),
        *_price_violations(plan),
        *_naming_violations(plan, product),
        *_image_brief_violations(plan, product),
    ]


def find_spotlight_flags(
    plan: SocialPostPlan, *, product: ProductEntry, mascot_name: str, mascot_facts: str
) -> list[str]:
    """Flag-only findings, de-duplicated, in a stable order. Never blocks.

    * ``FLAG_MASCOT_USAGE_UNVERIFIED`` -- some single sentence of either
      caption contains ``mascot_name`` (whole word, case-insensitive), a usage
      verb (eat/eats/ate, use/uses/used, love/loves/loved, chew/chews, get/gets,
      try/tried -- whole words) AND the brand token, while ``mascot_facts``
      does not mention the brand token. Skipped entirely when ``mascot_name``
      or the brand token is blank.

    ``lib.medical_claims_validator.find_banned_claims`` and
    ``FLAG_PRODUCT_NOT_IN_POST`` are NOT produced here: ``compose_spotlight``
    concatenates them (banned claims first, then this function's output, then
    the not-in-post flag), so each source stays independently testable.
    """
    name = mascot_name.strip()
    token_pattern = brand_token_re(product)
    if not name or token_pattern is None:
        # "Cannot check" -- a brand with no mascot, or a product whose display
        # name yields no token. Silence beats a flag nobody can act on.
        return []
    if token_pattern.search(mascot_facts or ""):
        # The facts file talks about this product, so a usage claim has a
        # source. Whether it is the RIGHT claim is the reviewer's call.
        return []
    name_pattern = re.compile(rf"\b{re.escape(name)}(?:['’]s)?\b", re.IGNORECASE)
    for caption in (plan.fb_caption, plan.ig_caption):
        for sentence in _SENTENCE_SPLIT_RE.split(caption):
            # All three in ONE sentence: across sentences the mascot and the
            # product are merely both present, which every spotlight does.
            if (
                name_pattern.search(sentence)
                and _USAGE_VERB_RE.search(sentence)
                and token_pattern.search(sentence)
            ):
                return [FLAG_MASCOT_USAGE_UNVERIFIED]
    return []
