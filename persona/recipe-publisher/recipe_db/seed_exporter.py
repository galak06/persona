"""Export a normalized, safety-checked ``RecipeRow`` into the seed library.

The seed library (``recipe-publisher/seeds/seeds.json``) is the frozen ground
truth consumed by ``generators/seeds.py::load_seeds``. This module builds a seed
dict that matches the ``RecipeSeed`` contract EXACTLY and merges it atomically
into the seeds file (replace-or-append by id), preserving all existing seeds and
the ``_schema_version`` / ``_note`` wrapper.

Synthesized fields (``storage`` and ``portion_guide``) are generic placeholders
intended for human review — they are intentionally non-specific so a reviewer
fills in real per-recipe guidance before publishing.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path

from recipe_db.models import Ingredient, RecipeRow
from recipe_db.safety import safety_note

logger = logging.getLogger(__name__)

_SEEDS_PATH = Path(__file__).resolve().parent.parent / "seeds" / "seeds.json"

_DEFAULT_CATEGORY = "treats"
_DEFAULT_YIELD = "varies"
_DEFAULT_STORAGE = (
    "Store in an airtight container in the fridge for up to 5 days, "
    "or freeze for up to 3 months."
)
# Generic, weight-based placeholders for human review — NOT invented specifics.
_DEFAULT_PORTION_GUIDE: dict[str, str] = {
    "small": "A small amount for dogs under 20 lb / 9 kg — adjust to your dog.",
    "medium": "A moderate amount for dogs 20-50 lb / 9-23 kg — adjust to your dog.",
    "large": "A larger amount for dogs over 50 lb / 23 kg — adjust to your dog.",
}

# Title words that carry no recipe signal (mirrors generators/seeds stopwords).
_TITLE_STOPWORDS = frozenset(
    {
        "dog", "dogs", "recipe", "recipes", "treat", "treats", "food",
        "homemade", "easy", "simple", "quick", "best", "top", "favorite",
        "for", "the", "and", "with", "from", "make", "making", "a", "an",
    }
)


def flatten_ingredient(ing: Ingredient) -> str:
    """Join qty + unit + item (+ notes in parens) into one clean line.

    Mirrors the existing seed style (e.g. "1 cup (120g) whole wheat flour").
    When qty/unit are empty, falls back to just the item. Collapses any
    incidental double spacing introduced by blank parts.
    """
    parts = [ing.qty.strip(), ing.unit.strip(), ing.item.strip()]
    line = " ".join(p for p in parts if p)
    notes = ing.notes.strip()
    if notes:
        line = f"{line} ({notes})" if line else f"({notes})"
    return re.sub(r"\s+", " ", line).strip()


def _topic_keywords(row: RecipeRow) -> list[str]:
    """Derive deduped, lowercased keyword signal from tags + title words."""
    keywords: list[str] = []
    seen: set[str] = set()

    def _add(token: str) -> None:
        norm = token.strip().lower()
        if norm and norm not in seen:
            seen.add(norm)
            keywords.append(norm)

    for tag in row.tags:
        _add(tag.replace("-", " "))
    for word in re.findall(r"[a-z]+", row.name.lower()):
        if len(word) > 2 and word not in _TITLE_STOPWORDS:
            _add(word)
    return keywords


def recipe_to_seed(row: RecipeRow) -> dict[str, object]:
    """Build the EXACT seed dict consumed by ``generators/seeds.load_seeds``."""
    return {
        "id": row.ensure_id(),
        "title": row.name,
        "topic_keywords": _topic_keywords(row),
        "category": row.category.strip() or _DEFAULT_CATEGORY,
        "prep_minutes": int(row.prep_minutes),
        "cook_minutes": int(row.cook_minutes),
        "yield_servings": row.servings.strip() or _DEFAULT_YIELD,
        "tags": list(row.tags),
        "ingredients": [flatten_ingredient(i) for i in row.ingredients],
        "steps": list(row.steps),
        "dog_safety_notes": safety_note(row.toxic_flags),
        "storage": _DEFAULT_STORAGE,
        "portion_guide": dict(_DEFAULT_PORTION_GUIDE),
        "source_attribution": (
            f"Adapted from {row.source_name} ({row.source_url})"
        ),
    }


def _load_seeds_file(path: Path) -> dict[str, object]:
    """Load the seeds wrapper, defaulting to an empty library shape."""
    if not path.exists():
        return {"_schema_version": "1", "_note": "", "seeds": []}
    data: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
    if "seeds" not in data or not isinstance(data["seeds"], list):
        raise ValueError(f"malformed seeds file (no 'seeds' list): {path}")
    return data


def _merge_seed(
    data: dict[str, object], seed: dict[str, object]
) -> dict[str, object]:
    """Replace-or-append ``seed`` by id, preserving order and wrapper keys."""
    raw_seeds = data["seeds"]
    seeds: list[dict[str, object]] = (
        list(raw_seeds) if isinstance(raw_seeds, list) else []
    )
    for index, existing in enumerate(seeds):
        if existing.get("id") == seed["id"]:
            seeds[index] = seed
            break
    else:
        seeds.append(seed)
    data["seeds"] = seeds
    return data


def _atomic_write(path: Path, data: dict[str, object]) -> None:
    """Write ``data`` as pretty JSON to a temp file, then os.replace it in."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def export_seed(
    row: RecipeRow,
    seeds_path: Path | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, object]:
    """Build and atomically merge a seed for ``row`` into the seeds file.

    Refuses to export rows that are not dog-safe unless ``row.override`` is set.
    On ``dry_run`` the seed dict is returned WITHOUT touching disk. The merge is
    idempotent: re-exporting the same id replaces it in place.
    """
    if not row.dog_safe and not row.override:
        raise ValueError(
            f"refusing to export unsafe recipe '{row.id or row.name}': "
            f"toxic_flags={row.toxic_flags}. Set override=True to force."
        )

    seed = recipe_to_seed(row)
    if dry_run:
        logger.info("dry-run: built seed '%s' (not written)", seed["id"])
        return seed

    path = seeds_path if seeds_path is not None else _SEEDS_PATH
    data = _load_seeds_file(path)
    data = _merge_seed(data, seed)
    _atomic_write(path, data)
    logger.info("exported seed '%s' -> %s", seed["id"], path)
    return seed
