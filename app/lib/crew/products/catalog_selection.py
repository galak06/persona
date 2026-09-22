"""The one-product-per-category rule, as pure operations on catalog entries.

Separate from `catalog_store` for the same reason `reference_library_edit` is
separate from `reference_library_store`: the store was at its line ceiling,
and these are a different kind of thing. Nothing here touches a file, takes a
lock, or knows what a brand is -- they are list-of-dicts transformations, so
the invariant can be tested exhaustively without a filesystem, and the store
stays responsible only for applying them inside its lock.

The rule itself: a focus category names AT MOST ONE product. Selecting a
product for a category therefore takes that category off whoever held it. A
selection is stored as the category's display spelling on the entry, and every
comparison slugifies, so a catalog that says "dental-care" and a registry that
says "Dental Care" agree about who is selected.
"""

from __future__ import annotations

from typing import Any

from lib.crew.products.focus import slugify_category


def tags_of(entry: dict[str, Any]) -> list[str]:
    """This entry's `selected_for` as a list of strings, whatever it holds.

    Hand-edited catalogs are the norm here, so a missing value, a bare string
    or a stray non-list all have to read as "no selections" rather than raise
    on a settings page.
    """
    raw = entry.get("selected_for")
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if not isinstance(raw, list):
        return []
    return [str(tag) for tag in raw]


def without_category(entry: dict[str, Any], category: str) -> list[str]:
    """This entry's selections with `category` removed, spelling-tolerant."""
    target = slugify_category(category)
    return [tag for tag in tags_of(entry) if slugify_category(tag) != target]


def holds_category(entry: dict[str, Any], category: str) -> bool:
    """Whether this entry is currently the one selected for `category`."""
    target = slugify_category(category)
    return any(slugify_category(tag) == target for tag in tags_of(entry))


def apply_exclusive(entries: list[dict[str, Any]], key: str, category: str) -> list[str]:
    """Give `category` to `key` alone; return the keys it was taken from.

    Mutates the entry dicts in place, because the caller holds them inside a
    `locked_json` block and the write-back is the whole transaction. The
    returned list is for logging: silently displacing the product that was
    being promoted yesterday is the kind of thing an operator should be able
    to find in a log afterwards.
    """
    target_key = key.strip().lower()
    displaced: list[str] = []
    for entry in entries:
        entry_key = str(entry.get("key", "")).strip().lower()
        trimmed = without_category(entry, category)
        if entry_key == target_key:
            entry["selected_for"] = [*trimmed, category]
            continue
        if len(trimmed) != len(tags_of(entry)):
            displaced.append(entry_key)
        entry["selected_for"] = trimmed
    return displaced
