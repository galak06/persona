"""Campaign-folder + Recipe-rehydration helpers shared by the workers.

The artifacts (jpg/mp4/captions) still live on disk under
``<BRAND_DIR>/campaigns/recipes/ready/<id>/`` — only the *indication* of what is
done lives in the DB. These helpers resolve that folder and rebuild the
brand-voice Recipe object a worker needs from a DB row.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from recipe_db.models import RecipeRow

if TYPE_CHECKING:
    from generators.image import GeneratedImage
    from generators.recipe import Recipe

_RP_ROOT = Path(__file__).resolve().parent.parent  # recipe-publisher/


def brand_dir() -> Path:
    """Brand data root (``BRAND_DIR``), falling back to the package dir."""
    brand = os.environ.get("BRAND_DIR")
    return Path(brand) if brand else _RP_ROOT


def artifacts_rel(row: RecipeRow) -> str:
    """BRAND_DIR-relative campaign folder for a recipe (the ready/ convention)."""
    return f"campaigns/recipes/ready/{row.id}"


def badge_path() -> str | None:
    """Path to the Nalla-approved seal PNG, or None when unset/missing.

    Stamped on the carousel POST hero only (never the reel). Returns None so
    callers fall back to the @handle pill rather than crashing.
    """
    brand = os.environ.get("BRAND_DIR")
    if not brand:
        return None
    badge = Path(brand) / "data" / "media" / "nalla-approved-badge.png"
    return str(badge) if badge.exists() else None


def save_images(images: list[GeneratedImage], folder: Path) -> int:
    """Write GeneratedImages as ``slide_N.jpg`` into folder. Returns the count."""
    folder.mkdir(parents=True, exist_ok=True)
    for i, img in enumerate(images, 1):
        (folder / f"slide_{i}.jpg").write_bytes(img.bytes_ or b"")
    return len(images)


def save_frames(frames: list[bytes], folder: Path) -> int:
    """Write raw image-byte frames as ``slide_N.jpg`` into folder. Returns count."""
    folder.mkdir(parents=True, exist_ok=True)
    for i, data in enumerate(frames, 1):
        (folder / f"slide_{i}.jpg").write_bytes(data)
    return len(frames)


def load_frames(folder: Path) -> list[bytes]:
    """Read ``slide_*.jpg`` byte frames from a folder, ordered by slide index.

    Numeric sort (not lexical) so slide_10 follows slide_9, not slide_1.
    """
    if not folder.exists():
        return []
    paths = sorted(
        folder.glob("slide_*.jpg"), key=lambda p: int(p.stem.split("_")[1])
    )
    return [p.read_bytes() for p in paths]


def campaign_folder(row: RecipeRow) -> Path:
    """Absolute campaign folder for a recipe.

    Prefers the stored ``artifacts_path`` (the ready/ location); falls back to
    published/ when Worker D has already moved the folder there. Returns the
    ready/ path when neither exists yet (so creators can mkdir it).
    """
    brand = brand_dir()
    if row.artifacts_path:
        ready = brand / row.artifacts_path
    else:
        ready = brand / "campaigns" / "recipes" / "ready" / row.id
    if ready.exists():
        return ready
    published = brand / "campaigns" / "recipes" / "published" / row.id
    if published.exists():
        return published
    return ready


def ensure_seed_exported(row: RecipeRow) -> None:
    """Idempotently export the row's frozen seed into seeds/seeds.json.

    ``generate_recipe(seed_id=...)`` resolves factual content from that seed, so
    it must exist before rehydration. Re-exporting replaces the seed in place.
    """
    from recipe_db import seed_exporter

    seed_exporter.export_seed(row)


def rehydrate_recipe(row: RecipeRow) -> Recipe:
    """Rebuild the brand-voice Recipe from a DB row via its frozen seed.

    Uses the deterministic ``seed_id`` branch (no fuzzy topic matching): factual
    content comes from seeds/seeds.json, the voice is regenerated each call.
    """
    from generators.recipe import generate_recipe

    from lib.local_env import get_brand_campaign

    hook_blocklist = (get_brand_campaign() or {}).get("hook_blocklist")
    return generate_recipe(row.name, seed_id=row.id, hook_blocklist=hook_blocklist)
