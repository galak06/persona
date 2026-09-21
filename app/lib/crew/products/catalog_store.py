"""Read-modify-write access to a brand's affiliate product catalog.

The catalog (`<brand>/data/config/affiliate_products.json`) was a hand-edited
file until the Brand Settings products panel existed; this module is what the
API writes through so that editing it from the UI is safe against the pipeline
reading it at the same moment. Every mutation runs inside `locked_json`, so a
draft resolving `[AFFILIATE:key]` placeholders mid-save sees either the old
file or the new one, never a half-written array.

Three rules the UI depends on and therefore cannot be relaxed here:

* **Entries are mutated in place, never rebuilt.** Real catalogs carry fields
  this module has never heard of -- the live dogfoodandfun file opens with a
  `_note` explaining the format to whoever next opens it in an editor, and
  entries carry `sourced_internal` provenance. Rebuilding an entry from known
  fields would silently drop all of it, so writers touch only the keys they
  were asked to change.
* **Keys are immutable.** A key is the contract with every published post's
  `[AFFILIATE:key]` placeholder; renaming one breaks live pages. Getting a key
  wrong means deactivating the entry and adding a new one.
* **There is no delete.** Deactivation (`active=False`) hides an entry from
  selection while leaving it resolvable, because posts published while it was
  active still link to it.
* **One selected product per category.** Selecting a product for a category
  deselects whatever held it, inside the same lock. The invariant is enforced
  here rather than in the route because it is a property of the data, not of
  the screen: two operators selecting two different products for Dental Care
  from two tabs must not both win, and a read-then-write in the API could not
  prevent that.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from lib.crew.products.catalog_selection import apply_exclusive, without_category
from lib.io.jsonio import locked_json
from lib.observability import get_logger

logger = get_logger(__name__)

CATALOG_RELATIVE = Path("data") / "config" / "affiliate_products.json"

# `lib.affiliate_resolver._PLACEHOLDER_RE` decides what a post can actually
# reference, so a key the UI accepts but that regex rejects would be a product
# nobody can link. Kept deliberately identical to it.
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")


class CatalogStoreError(RuntimeError):
    """A catalog edit that must not be applied (bad input or a key clash)."""


def _empty() -> list[dict[str, Any]]:
    """A fresh empty catalog body for `locked_json`'s `default`.

    A function rather than a module constant on purpose: `locked_json` yields
    the default itself when the file is missing, and the block then mutates
    and writes back whatever it was handed. A shared list would accumulate
    every brand's first product.
    """
    return []


def catalog_path(brand_dir: Path) -> Path:
    """Where this brand's flat affiliate catalog lives."""
    return brand_dir / CATALOG_RELATIVE


def load_raw(brand_dir: Path) -> list[dict[str, Any]]:
    """Every catalog entry as stored, newest edits included.

    Read-only and tolerant: a brand with no catalog yet reads as `[]`, which
    the panel renders as an empty products list rather than an error, and a
    malformed file reads as `[]` with a warning rather than taking the
    settings page down. Non-dict members are skipped -- the file is
    hand-editable, so a stray string in the array is possible and is not a
    reason to refuse the other 37 products.
    """
    path = catalog_path(brand_dir)
    if not path.exists():
        return []
    try:
        raw = _read_json_array(path)
    except ValueError as exc:
        logger.warning("products_catalog_unreadable", path=str(path), error=str(exc))
        return []
    return [entry for entry in raw if isinstance(entry, dict)]


def add_product(
    brand_dir: Path,
    *,
    key: str,
    asin: str,
    display: str,
    category: str | None = None,
    notes: str | None = None,
    select_for: str | None = None,
) -> dict[str, Any]:
    """Append a new product and return the stored entry.

    `select_for` selects the new product for that category, which by the
    one-per-category rule deselects whichever product held it.

    Raises `CatalogStoreError` if the key is malformed or already taken. The
    duplicate check is deliberately inside the lock: two operators adding the
    same key from two tabs must not both succeed, and the resolver raises on a
    duplicate key at load time, so letting one through would break every
    subsequent draft rather than just this request.
    """
    normalized_key = (key or "").strip().lower()
    _validate_key(normalized_key)
    normalized_asin = (asin or "").strip().upper()
    _validate_asin(normalized_asin)
    label = (display or "").strip() or normalized_key

    entry: dict[str, Any] = {
        "key": normalized_key,
        "asin": normalized_asin,
        "display": label,
        "active": True,
        "selected_for": [],
    }
    if category and category.strip():
        entry["category"] = category.strip()
    if notes and notes.strip():
        entry["notes"] = notes.strip()

    with locked_json(catalog_path(brand_dir), default=_empty()) as data:
        entries = _usable(data)
        if _find(entries, normalized_key) is not None:
            raise CatalogStoreError(f"product key already exists: {normalized_key!r}")
        data.append(entry)
        if select_for and select_for.strip():
            apply_exclusive(_usable(data), normalized_key, select_for.strip())

    logger.info(
        "products_catalog_entry_added",
        brand_id=brand_dir.name,
        key=normalized_key,
        category=entry.get("category"),
    )
    return entry


def update_product(
    brand_dir: Path,
    key: str,
    *,
    display: str | None = None,
    category: str | None = None,
    notes: str | None = None,
    active: bool | None = None,
    selected_for: list[str] | None = None,
) -> dict[str, Any]:
    """Change fields on an existing product and return the stored entry.

    `None` means "leave this field alone", which is what lets the panel PATCH
    a single toggle without having to round-trip every other field and risk
    clobbering an edit made in another tab. Passing `selected_for=[]` is
    therefore a real instruction -- clear every selection -- and is how a
    product is taken out of the focus run without deactivating it.
    """
    normalized_key = (key or "").strip().lower()
    with locked_json(catalog_path(brand_dir), default=_empty()) as data:
        entry = _find(_usable(data), normalized_key)
        if entry is None:
            raise CatalogStoreError(f"no such product key: {normalized_key!r}")
        if display is not None and display.strip():
            entry["display"] = display.strip()
        if category is not None:
            entry["category"] = category.strip()
        if notes is not None:
            entry["notes"] = notes.strip()
        if active is not None:
            entry["active"] = active
        if selected_for is not None:
            entry["selected_for"] = _clean_tags(selected_for)
        stored = dict(entry)

    logger.info(
        "products_catalog_entry_updated",
        brand_id=brand_dir.name,
        key=normalized_key,
        active=stored.get("active", True),
        selected_for=stored.get("selected_for", []),
    )
    return stored


def _read_json_array(path: Path) -> list[Any]:
    """The catalog file parsed, insisting it really is a JSON array."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"expected a JSON array, got {type(raw).__name__}")
    return raw


def _usable(data: Any) -> list[dict[str, Any]]:
    """The dict entries of a locked catalog body."""
    if not isinstance(data, list):
        return []
    return [entry for entry in data if isinstance(entry, dict)]


def _find(entries: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    """The live entry dict for `key`, so callers mutate the stored object."""
    for entry in entries:
        if str(entry.get("key", "")).strip().lower() == key:
            return entry
    return None


def _clean_tags(tags: list[str] | None) -> list[str]:
    """Selection tags trimmed, blanks dropped, order kept, duplicates removed."""
    seen: dict[str, None] = {}
    for tag in tags or []:
        if text := str(tag or "").strip():
            seen.setdefault(text, None)
    return list(seen)


def _validate_key(key: str) -> None:
    if not _KEY_RE.match(key):
        raise CatalogStoreError(
            f"invalid product key {key!r}: must match [a-z0-9][a-z0-9_-]* so "
            "that [AFFILIATE:key] placeholders can reference it"
        )


def _validate_asin(asin: str) -> None:
    if not _ASIN_RE.match(asin):
        raise CatalogStoreError(
            f"invalid ASIN {asin!r}: expected the 10-character id from amazon.com/dp/<ASIN>"
        )


def set_focus_selection(
    brand_dir: Path, key: str, category: str, *, selected: bool
) -> dict[str, Any]:
    """Select or deselect one product for one focus category.

    Selecting is exclusive: the category is taken off every other entry first,
    so a category always names at most one product. Deselecting only clears
    this entry, leaving the category unowned.

    Selections for OTHER categories are never touched, so rotating the focus
    to Grooming and back to Dental Care finds the dental pick still standing.
    """
    normalized_key = (key or "").strip().lower()
    label = (category or "").strip()
    if not label:
        raise CatalogStoreError("cannot select a product for a blank category")

    with locked_json(catalog_path(brand_dir), default=_empty()) as data:
        entries = _usable(data)
        entry = _find(entries, normalized_key)
        if entry is None:
            raise CatalogStoreError(f"no such product key: {normalized_key!r}")
        displaced: list[str] = []
        if selected:
            displaced = apply_exclusive(entries, normalized_key, label)
        else:
            entry["selected_for"] = without_category(entry, label)
        stored = dict(entry)

    logger.info(
        "products_catalog_selection_set",
        brand_id=brand_dir.name,
        key=normalized_key,
        category=label,
        selected=selected,
        displaced=displaced,
    )
    return stored
