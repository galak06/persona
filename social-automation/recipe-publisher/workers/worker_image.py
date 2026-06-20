"""Worker E — hero image.

For every recipe that has a WordPress post but no hero image yet, generate the
image and write it to the campaign folder as ``post_image.jpg``, then stamp the
DB with the ISO timestamp.

Poll predicate (idempotent — no other worker's state referenced):
    wp_url truthy  AND  image_created_at == ""

On success it writes ``post_image.jpg`` to the campaign folder and sets
``image_created_at`` in the DB.

    python -m workers.worker_image                   # dry-run plan
    python -m workers.worker_image --apply --limit 1 # generate one
    python -m workers.worker_image --health-check    # image provider check → 0/1
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
# Ensure recipe-publisher/ is on sys.path when run as a script (not as a module)
_rp_root = _Path(__file__).resolve().parent.parent  # → recipe-publisher/
if str(_rp_root) not in _sys.path:
    _sys.path.insert(0, str(_rp_root))

import datetime
import logging
import os
from typing import TYPE_CHECKING

from recipe_db.models import RecipeRow
from recipe_db.repository import RecipeRepository

from workers._base import run_worker
from workers._folder import campaign_folder, rehydrate_recipe

if TYPE_CHECKING:
    pass

logger = logging.getLogger("workers.image")


def _targets(
    repo: RecipeRepository, seeds: list[str], limit: int
) -> list[RecipeRow]:
    """Rows with a WP post but no hero image yet."""
    rows = [
        r
        for r in repo.list_recipes()
        if r.wp_url and r.image_created_at == ""
        and (not seeds or r.id in seeds)
    ]
    rows.sort(key=lambda r: r.id)
    return rows[:limit] if limit else rows


def _do_one(repo: RecipeRepository, row: RecipeRow) -> str:
    """Generate the hero image and save it to the campaign folder."""
    from generators.image import generate_image

    recipe = rehydrate_recipe(row)  # noqa: F841 — side effects: seed export + voice warm-up
    brief = f"dog food recipe photo: {row.name}"
    img = generate_image(brief, alt_hint=row.name)

    folder = campaign_folder(row)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "post_image.jpg").write_bytes(img.bytes_ or b"")

    ts = datetime.datetime.utcnow().isoformat()
    repo.set_image_created_at(row.id, ts)

    return "image"


def _health() -> bool:
    """At least one image-provider credential is configured."""
    providers = [
        os.getenv("GEMINI_API_KEY"),
        os.getenv("PEXELS_API_KEY"),
        os.getenv("FALLBACK_IMAGE_URL"),
    ]
    if any(providers):
        return True
    logger.error(
        "no image provider configured — set GEMINI_API_KEY, PEXELS_API_KEY, "
        "or FALLBACK_IMAGE_URL"
    )
    return False


def main(argv: list[str] | None = None) -> int:
    return run_worker(
        "image",
        targets_fn=_targets,
        do_one_fn=_do_one,
        health_fn=_health,
        argv=argv,
    )


if __name__ == "__main__":
    import sys

    sys.exit(main())
