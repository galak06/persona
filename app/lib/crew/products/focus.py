"""Restricting the candidate pool to the products chosen for the focus run.

The brand's one-focus-category strategy (`brands.focus_category`) commits the
site to a single subject for months at a time. This module is the product half
of that commitment: which entries of the catalog are allowed to be promoted
while a focus is in force.

Selection is EXPLICIT, not inferred from `ProductEntry.category`. A category
tag describes what a product is; it says nothing about whether the operator
wants it promoted this run, and inferring consent from a tag would silently
enrol every newly-tagged product into a live campaign. So an entry opts in per
category, via `selected_for`, and the gate is absolute: with a focus set, a
product that was never selected for it is not a candidate, no matter how well
it scores or how empty that leaves the pool.

Two normalizations make the comparison survive real data. The registry stores
a display string ("Dental Care") while catalogs store slugs ("dental-care"),
so both sides are slugified before they meet. And `active=False` entries are
dropped everywhere -- with or without a focus -- because deactivation is this
catalog's delete: the row stays so published `[AFFILIATE:key]` placeholders
keep resolving, but it must never be picked again.
"""

from __future__ import annotations

import re
import unicodedata

from lib.affiliate_resolver import ProductEntry
from lib.observability import get_logger

logger = get_logger(__name__)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify_category(text: str) -> str:
    """A category label reduced to its comparable slug.

    "Dental Care" and "dental-care" are the same category written by two
    different parts of the system (the brand registry vs. the catalog file),
    so every comparison in this module goes through here first. Accented
    characters fold to ASCII rather than dropping out, so "Éclat" slugifies to
    "eclat" and not "clat".

    Deliberately NOT `lib.content_strategy.normalize_category`: that one
    casefolds and collapses whitespace but keeps punctuation, on purpose, so
    that "Dog Food" and "Dog Foods" stay distinct WordPress categories. It
    therefore leaves "Dental Care" and "dental-care" unequal -- which is the
    exact gap this function exists to close. Two different jobs, both correct.
    """
    folded = unicodedata.normalize("NFKD", text or "")
    ascii_only = folded.encode("ascii", "ignore").decode("ascii").lower()
    return _NON_ALNUM.sub("-", ascii_only).strip("-")


def is_selected_for(entry: ProductEntry, focus_slug: str) -> bool:
    """Whether `entry` was explicitly opted in to the `focus_slug` category."""
    return any(slugify_category(tag) == focus_slug for tag in entry.selected_for)


def focus_pool(pool: dict[str, ProductEntry], focus_category: str) -> dict[str, ProductEntry]:
    """The subset of `pool` eligible to be promoted right now.

    With no focus declared (`focus_category=""`) this is the pre-focus
    behaviour with one addition: inactive entries are still excluded, because
    deactivation is a statement about the product, not about the campaign.

    With a focus declared the gate is absolute -- only entries explicitly
    selected for that category survive. An empty result is a legitimate
    answer meaning "nothing is selected for this focus yet", which the
    selector's existing empty-pool branch already renders as "omit the
    product block" rather than an error.
    """
    active = {key: entry for key, entry in pool.items() if entry.active}
    inactive_count = len(pool) - len(active)

    focus_slug = slugify_category(focus_category)
    if not focus_slug:
        if inactive_count:
            logger.info("crew_products_focus_inactive_excluded", count=inactive_count)
        return active

    selected = {key: entry for key, entry in active.items() if is_selected_for(entry, focus_slug)}
    logger.info(
        "crew_products_focus_gated",
        focus_category=focus_category,
        focus_slug=focus_slug,
        pool_size=len(pool),
        inactive_excluded=inactive_count,
        selected_count=len(selected),
    )
    if not selected:
        logger.warning(
            "crew_products_focus_pool_empty",
            focus_category=focus_category,
            focus_slug=focus_slug,
            hint="no catalog entry is selected for this focus category",
        )
    return selected
