"""Recipe-card input model + loader from a ready/ campaign folder.

Pulls title / ingredients / steps / timing from the campaign's seed entry in
``recipe-publisher/seeds/seeds.json`` (structured), falling back to the folder's
``metadata.json`` for the title. Image paths resolve to the campaign folder's
``featured.jpg`` and ``slides/slide_*.jpg``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SEEDS_PATH = REPO_ROOT / "recipe-publisher" / "seeds" / "seeds.json"


def _brand_dir() -> Path:
    """Resolve the brand dir from ``BRAND_DIR`` (recipe-publisher convention).

    Mirrors ``recipe-publisher/prepare.py``: read the ``BRAND_DIR`` env, falling
    back to the sibling ``persona`` dir when unset so local runs still work.
    """
    brand_dir = os.environ.get("BRAND_DIR")
    if brand_dir:
        return Path(brand_dir)
    return REPO_ROOT.parent / "persona"


CAMPAIGNS_ROOT = _brand_dir() / "campaigns" / "recipes" / "ready"


@dataclass
class RecipeCardData:
    """Everything a recipe-card template needs, fully resolved."""

    seed_id: str
    title: str
    ingredients: list[str]
    steps: list[str] = field(default_factory=list)
    prep_minutes: int | None = None
    cook_minutes: int | None = None
    yield_servings: str = ""
    hero_path: Path | None = None
    slide_paths: list[Path] = field(default_factory=list)


def _load_seed(seed_id: str) -> dict | None:
    if not SEEDS_PATH.exists():
        return None
    data = json.loads(SEEDS_PATH.read_text(encoding="utf-8"))
    for seed in data.get("seeds", []):
        if seed.get("id") == seed_id:
            return seed
    return None


def _load_metadata(folder: Path) -> dict:
    meta = folder / "metadata.json"
    if meta.exists():
        return json.loads(meta.read_text(encoding="utf-8"))
    return {}


def load_recipe_card(seed_id: str) -> RecipeCardData:
    """Build a :class:`RecipeCardData` for ``seed_id`` from real campaign data.

    Raises ``FileNotFoundError`` if the campaign folder or its hero image is
    missing so callers can substitute another ready/ folder.
    """
    folder = CAMPAIGNS_ROOT / seed_id
    if not folder.is_dir():
        raise FileNotFoundError(f"campaign folder missing: {folder}")

    seed = _load_seed(seed_id) or {}
    meta = _load_metadata(folder)

    title = seed.get("title") or meta.get("title") or seed_id
    ingredients = list(seed.get("ingredients", []))
    steps = list(seed.get("steps", []))

    hero = folder / "featured.jpg"
    if not hero.exists():
        raise FileNotFoundError(f"featured.jpg missing in {folder}")

    slides_dir = folder / "slides"
    slides = sorted(slides_dir.glob("slide_*.jpg")) if slides_dir.is_dir() else []

    return RecipeCardData(
        seed_id=seed_id,
        title=title,
        ingredients=ingredients,
        steps=steps,
        prep_minutes=seed.get("prep_minutes"),
        cook_minutes=seed.get("cook_minutes"),
        yield_servings=str(seed.get("yield_servings", "")),
        hero_path=hero,
        slide_paths=slides,
    )
