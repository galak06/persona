"""The spotlight writer's brief: the social-post brief plus one override section.

CONTRACT FROZEN BY PHASE 0 -- the constants below are REAL and final (the API
and the rules import them today); the two function signatures and the
behaviour their docstrings spell out are final. Phase 1C replaces the
``NotImplementedError`` bodies and changes nothing else.

``lib/crew/socialpost/prompts.py`` is NOT edited. A spotlight is the same
deliverable -- two captions, one shared hook image, a comment-keyword CTA --
aimed at one product, so the description is
``build_social_post_task_description(...)`` with a section APPENDED that
overrides where the two differ. Appending (rather than forking the base text)
keeps every improvement to the base brief flowing into spotlights for free; the
price is drift between the two texts, which ``tests/test_spotlight_prompts.py``
and the blocking rules in ``rules.py`` exist to catch.

Nothing here reads a file or the environment: the caller passes
``mascot_facts`` (``lib.crew.writer.context.mascot_facts_summary(brand_dir)``)
and ``brand_voice`` (``lib.crew.context.brand_voice_summary(brand_dir)``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

from lib.affiliate_resolver import ProductEntry
from lib.certification_claims import CERTIFICATIONS
from lib.crew.socialpost.prompts import build_social_post_task_description
from lib.crew.spotlight.product_terms import (
    brand_token,
    display_name,
    verified_certifications,
)

#: The affiliate disclosure, VERBATIM in both captions. Also returned by the
#: API as ``dm_disclosure`` so the owner's manual DM carries the same sentence.
#: Not ``lib.crew.products.block.DISCLOSURE``: that one is blog copy ("We only
#: recommend gear we actually use") and makes a usage claim a spotlight of a
#: not-yet-used product could not honestly repeat.
DISCLOSURE_SENTENCE: Final[str] = "As an Amazon Associate I earn from qualifying purchases."

#: What ``rules.find_spotlight_violations`` looks for (lower-cased caption).
#: A marker rather than the whole sentence so a model that adds a comma is
#: corrected by the prompt, not failed by the rule.
DISCLOSURE_MARKER: Final[str] = "as an amazon associate"

#: ``spotlight_keyword`` keeps at most this many meaningful terms.
KEYWORD_MAX_TERMS: Final[int] = 5


def spotlight_keyword(product: ProductEntry) -> str:
    """The ``target_keyword`` a spotlight is written and checked against.

    ``product_terms.display_name(product)`` (display cut at the first ``(``),
    then capped at ``KEYWORD_MAX_TERMS`` whitespace-separated words, original
    casing kept: "Greenies Regular Dental Dog Treats (36 ct)" -> "Greenies
    Regular Dental Dog Treats".

    The cap matters because of what consumes it:
    ``lib.crew.socialpost.rules.find_caption_violations`` demands two thirds of
    the keyword's meaningful terms in each caption's FIRST sentence. An
    eight-word catalog name would make that sentence unwritable.
    """
    return " ".join(display_name(product).split()[:KEYWORD_MAX_TERMS])


def _product_block(product: ProductEntry) -> str:
    """The one product, as the writer's only permitted subject.

    Deliberately NOT the raw ``ProductEntry``: the ASIN is the one field that
    turns a caption into a link-bearing caption if the model decides to be
    helpful, and there is no reason for the writer to ever see it.
    """
    note = (product.notes or "").strip()
    category = (product.category or "").strip()
    return f"""
## The ONE product this post is about
- Name: {display_name(product)}
- Category: {category or "(uncategorised)"}
- Catalog note (a SOURCE -- see the grounding rules below):
  {note or "(nothing recorded: the post excerpt is your only source)"}

Write about this product and nothing else -- no second product, no comparison \
line-up. Never write its ASIN, any product code, any link of any kind, or any \
price or discount. Prices go stale within hours and quoting one in an affiliate \
post breaks the programme's operating agreement outright.

Both `fb_caption` and `ig_caption` must NAME the product in words -- the brand \
word at minimum -- not "this treat" or "the one I use"."""


def _cta_block() -> str:
    """The comment-keyword CTA, restated because a spotlight's DM carries a
    purchase link, not an article link. Fulfilment is still a person."""
    return """
## The CTA (overrides the wording in the brief above)
Both captions end their traffic ask with this shape, using the ALL-CAPS \
`comment_keyword` you picked, verbatim:

    Comment {comment_keyword} and I'll DM you the link

A human sends that DM. NEVER promise a bot, an auto-reply, an instant link, a \
"link in bio", or anything automatic -- the account that promises automation it \
does not have is the account that gets reported."""


def _disclosure_block() -> str:
    """Where the disclosure goes, and the explicit resolution of the conflict
    with the base brief's "end with a question" rule. Left implicit, the model
    picks one rule and drops the other; naming the conflict and ordering the
    two lines costs three sentences and removes a whole retry class.
    """
    return f"""
## Affiliate disclosure (non-negotiable, both captions)
This exact sentence appears in BOTH captions, word for word, unaltered:

    {DISCLOSURE_SENTENCE}

- `fb_caption`: the brief above says to end with a genuine question. Keep that \
question -- then put the disclosure on its OWN line AFTER it. The question is \
the last thing you say to the reader; the disclosure is the footer under it.
- `ig_caption`: the disclosure goes on its own line BEFORE the hashtag line, so \
the hashtags stay the final line.
- Do not paraphrase it, do not shorten it to "#ad", do not wrap it in brackets. \
A reworded disclosure is a missing disclosure."""


def _grounding_block(*, mascot_facts: str, product_in_post: bool) -> str:
    """Every claim must trace to a source the caller actually supplied."""
    facts = mascot_facts.strip()
    facts_section = (
        f"""
### What is actually true about the brand's dog
{facts}
"""
        if facts
        else ""
    )
    not_in_post = (
        ""
        if product_in_post
        else """
- This product is NOT linked or discussed in that article. Do not say the post \
reviews it, tests it, ranks it, recommends it or mentions it at all, and do not \
say "as I wrote about" or "in my review". The article is background for the \
TOPIC; the product claim has to stand on the catalog note alone."""
    )
    return f"""
## Grounding: where every claim may come from
You may state ONLY what one of these three sources says: the post excerpt \
above, the catalog note above, and the dog facts below (when present). \
Everything else -- ingredient effects, vet opinions, test results, how long it \
lasts, how much dogs like it -- is invented, and an invented claim about a real \
product someone can buy is the kind that ends an affiliate account.
{facts_section}
- NEVER say the dog eats, chews, uses, wears, loves or asks for this product \
unless one of those sources says so in as many words. "Nalla's favourite" about \
a product nobody has confirmed she has ever had is a fabrication, not a flourish.{not_in_post}"""


def _certification_block(product: ProductEntry) -> str:
    """Which registry, if any, this product may be said to be accepted by."""
    verified = verified_certifications(product)
    bodies = ", ".join(CERTIFICATIONS)
    if not verified:
        return f"""
## Certifications: name NONE
Do not write {bodies}, "registry accepted", "vet approved" or any equivalent \
anywhere in the captions or the overlay text. Nobody has checked this product \
against a registry, and a certification nobody verified is the single most \
expensive sentence you can write here."""
    allowed = ", ".join(verified)
    others = ", ".join(c for c in CERTIFICATIONS if c not in verified) or "any other registry"
    return f"""
## Certifications: {allowed} ONLY
An operator verified this product against {allowed}, so you MAY say so (e.g. \
"{verified[0]}-accepted"). That is the COMPLETE list -- naming {others} is \
forbidden, because one registry was checked and the others were not. If the \
line reads better without the certification at all, leave it out."""


def _image_block(*, product: ProductEntry, photo_is_owner_pick: bool) -> str:
    """Art direction for a product post that must not show the product.

    An image model asked for a branded bag paints a plausible, entirely fake
    label onto a real product -- a misrepresentation of the thing being sold.
    The scene carries the post; the product is named in the caption.
    """
    token = brand_token(product)
    brand_clause = f' or the word "{token}"' if token else ""
    owner_clause = (
        """
- The reference photo is one the owner chose deliberately. Do NOT redraw or \
restyle anything already in the reference photo -- keep its dog, its setting, \
its light and its framing, and describe only what the scene is doing."""
        if photo_is_owner_pick
        else ""
    )
    return f"""
## The image: the SCENE only, never the product
`image_brief` describes a real physical moment -- the dog, hands, the setting, \
the light, the camera angle. It must NOT contain packaging, a package, a label, \
a logo, a bag, a box, brand text{brand_clause}, or any readable surface. There \
is no shot of the product: the caption names it, the picture sells the moment.{owner_clause}

`cta_ribbon_text` stays a short all-caps line about the guide or the site. It \
may not name a retailer or a store, and it may not carry a price, a discount or \
a percentage."""


def _override_section(
    *,
    product: ProductEntry,
    mascot_facts: str,
    photo_is_owner_pick: bool,
    product_in_post: bool,
) -> str:
    """The appended block. Fully self-contained so a reader of the final prompt
    can see every spotlight-specific rule in one place."""
    return f"""

---
# OVERRIDE -- AFFILIATE PRODUCT SPOTLIGHT
Everything above still applies (answer first, no links in either caption, the \
comment-keyword CTA, hashtag counts, the closing question) EXCEPT where this \
section says otherwise. Where the two disagree, THIS section wins.
{_product_block(product)}
{_cta_block()}
{_disclosure_block()}
{_grounding_block(mascot_facts=mascot_facts, product_in_post=product_in_post)}
{_certification_block(product)}
{_image_block(product=product, photo_is_owner_pick=photo_is_owner_pick)}
"""


def build_spotlight_task_description(
    *,
    title: str,
    body: str,
    product: ProductEntry,
    site_domain: str,
    brand_voice: str,
    mascot_facts: str,
    photo_is_owner_pick: bool,
    product_in_post: bool,
    reference_categories: Sequence[str] = (),
    reference_descriptions: Mapping[str, str] | None = None,
) -> str:
    """``build_social_post_task_description(title=, body=,
    target_keyword=spotlight_keyword(product), site_domain=, brand_voice=,
    reference_categories=, reference_descriptions=)`` + the override section.

    ``body`` is the caller's ``post_products.product_excerpt(...)``.

    The appended section MUST say, in this order of importance:

    * ONE product: its ``display_name``, ``category`` and catalog ``notes`` --
      never its ASIN, a URL or a price. Both captions name the product.
    * CTA: "Comment {KEYWORD} and I'll DM you the link". Never promise a bot or
      an instant/automatic reply -- a person sends the DM.
    * Both captions carry ``DISCLOSURE_SENTENCE`` verbatim. Facebook: the last
      line, AFTER the closing question (the base brief says "end with a
      question"; the override must resolve that conflict explicitly).
      Instagram: on its own line before the hashtag line.
    * Claims come ONLY from the post excerpt, the catalog note and
      ``mascot_facts``. Never say the mascot eats, uses or loves the product
      unless one of those sources says so. When ``mascot_facts`` is ``""``
      omit its block rather than sending an empty header.
    * ``product_in_post`` is ``False`` -> do not claim the article reviews,
      tests or mentions the product.
    * A certification only if it is in
      ``product_terms.verified_certifications(product)``, and only that one;
      when that tuple is empty, name no certification at all.
    * ``image_brief`` = the scene only (dog, hands, setting, light): no
      packaging, label, logo, bag, box or brand text.
      ``photo_is_owner_pick`` is ``True`` -> add "do not redraw or restyle
      anything already in the reference photo".
    * Ribbon (``cta_ribbon_text``): no retailer names, no prices.

    The returned text never contains ``product.asin``, ``/dp/`` or ``http``
    (the base brief does say the literal ``"www."`` while forbidding it, so do
    not assert on that).
    """
    base = build_social_post_task_description(
        title=title,
        body=body,
        target_keyword=spotlight_keyword(product),
        site_domain=site_domain,
        brand_voice=brand_voice,
        reference_categories=reference_categories,
        reference_descriptions=reference_descriptions,
    )
    return base + _override_section(
        product=product,
        mascot_facts=mascot_facts,
        photo_is_owner_pick=photo_is_owner_pick,
        product_in_post=product_in_post,
    )
