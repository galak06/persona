"""What a spotlight may call its product, and what it may claim about it.

Complete as of phase 0, and deliberately so. Four other modules need these
answers -- ``post_products`` (where in the article is this product discussed),
``prompts`` (which certification may the writer name), ``rules`` (did the
caption name the product; did the image brief name the brand) and the products
API route (the "certification verified" badge). If each derived its own notion
of "the brand word" the prompt would ask for one spelling while the rule
checked another, and a compliant caption would burn all three crew attempts
against a rule it could never satisfy. One definition, written before any of
its callers.

Pure functions over ``ProductEntry``: no I/O, no brand directory, no LLM.
"""

from __future__ import annotations

import re
from typing import Final

from lib.affiliate_resolver import ProductEntry
from lib.certification_claims import CERTIFICATIONS, VERIFIED_MARKER

_WORD_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z][A-Za-z0-9]*")

#: Leading words that are not a brand. Catalog display names open with them
#: often enough ("Canine Greenies", "Pet Honesty Dental Wipes") that taking the
#: first word blindly would demand "canine" in a caption that correctly says
#: "Greenies". Kept SHORT on purpose: every word added here is a word that can
#: no longer be a brand, and a wrong token fails closed (see ``brand_token``).
_GENERIC_LEADING_WORDS: Final[frozenset[str]] = frozenset(
    {
        "a",
        "an",
        "the",
        "new",
        "original",
        "premium",
        "natural",
        "organic",
        "canine",
        "dog",
        "dogs",
        "puppy",
        "pet",
        "pets",
    }
)


def display_name(product: ProductEntry) -> str:
    """The product's name as a person would say it: ``display`` cut at the
    first ``(``, so "Greenies Regular Dental Dog Treats (36 ct)" loses the pack
    size. Falls back to the catalog key when the entry has no display text."""
    name = (product.display or "").split("(", 1)[0].strip()
    return name or product.key.replace("-", " ").strip()


def brand_token(product: ProductEntry) -> str:
    """The ONE lowercase word that identifies this product in running text.

    The first word of ``display_name`` that is not a generic opener; when every
    word is generic, the first word anyway. "Greenies Regular Dental Dog
    Treats" -> ``greenies``; "Canine Greenies" -> ``greenies``; "Pet Honesty
    Dental Wipes" -> ``honesty``; "Fi Series 3+ Smart Dog Collar" -> ``fi``.

    Biased toward the FIRST word because the blocking rule built on it
    ("the caption must name the product") costs a paid crew retry when it is
    wrong: a caption that names the product at all almost always carries the
    word its name starts with. A token that is too generic merely weakens the
    rule, and a human still reviews every spotlight; a token the caption cannot
    reasonably contain fails the whole composition.

    Returns ``""`` only when both ``display`` and ``key`` are blank. Callers
    must treat that as "cannot check" and skip brand-token rules, never as
    "absent".
    """
    # `findall` is typed `list[Any]`; `finditer` + `group()` keeps this `str`.
    words = [m.group(0).lower() for m in _WORD_RE.finditer(display_name(product))]
    if not words:
        return ""
    for word in words:
        if word not in _GENERIC_LEADING_WORDS:
            return word
    return words[0]


def brand_token_re(product: ProductEntry) -> re.Pattern[str] | None:
    """Case-insensitive WHOLE-WORD matcher for ``brand_token``, or ``None``
    when there is no token.

    Whole-word, with an optional plural/possessive tail, because tokens can be
    two letters long: a prefix match on ``fi`` would find the brand in "first"
    and "find", and the image-brief rule would then block a brief that never
    mentioned it. ``.finditer`` on the result is how ``post_products`` locates
    the article passages that discuss the product.
    """
    token = brand_token(product)
    if not token:
        return None
    return re.compile(rf"\b{re.escape(token)}(?:['’]s|s)?\b", re.IGNORECASE)


def mentions_brand(text: str, product: ProductEntry) -> bool:
    """Whether ``text`` names the product's brand token. ``False`` for empty
    text and for a product with no token."""
    pattern = brand_token_re(product)
    return bool(pattern and text and pattern.search(text))


def is_certification_verified(product: ProductEntry) -> bool:
    """Whether an operator recorded a registry check in this product's note.

    The same test ``lib.certification_claims.verified_asins`` applies to the
    raw catalog (that function takes ``list[dict]``, not ``ProductEntry``,
    which is why it is not called here): the note contains
    ``VERIFIED_MARKER``. Absence means "not verified", never "not accepted".
    """
    return VERIFIED_MARKER in (product.notes or "").lower()


def verified_certifications(product: ProductEntry) -> tuple[str, ...]:
    """The certification bodies this product may be said to be accepted by.

    Empty unless the note is verified; then exactly the ``CERTIFICATIONS``
    tokens the note itself names. A verified VOHC note does not license an
    AAFCO claim -- the operator checked one list, not all of them.
    """
    if not is_certification_verified(product):
        return ()
    note = product.notes or ""
    return tuple(c for c in CERTIFICATIONS if re.search(rf"\b{c}\b", note, re.IGNORECASE))
