"""Regenerate the hero (featured) image for already-published recipes.

Swaps ONLY the featured image of each live WP post — the post body, PDF, and
publish_status are untouched. Uses the updated image pipeline (candid,
everyday-home styling; the dog reads as Nalla, a fluffy shepherd mix; no human
cutlery/place settings). The per-recipe brief comes straight from the voice
drafter's ``image_brief`` (no full-recipe re-validation, so the flaky
meta-length gate is bypassed).

For each recipe it: drafts an image_brief -> generates the image -> uploads +
sets it as the WP featured image (featured_media + FIFU meta) -> overwrites the
local ``recipe_artifacts/<id>/images/featured.jpg``.

    python -m scripts.regen_hero_images                    # dry-run plan
    python -m scripts.regen_hero_images --apply --limit 1  # one (validate)
    python -m scripts.regen_hero_images --apply            # all published
    python -m scripts.regen_hero_images --apply --seed <id>
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_RP = Path(__file__).resolve().parent.parent
_SA = _RP.parent
for _p in (str(_SA), str(_RP)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from lib.local_env import load_local_env  # noqa: E402
from recipe_db import db  # noqa: E402
from recipe_db.models import RecipeRow  # noqa: E402
from recipe_db.repository import RecipeRepository  # noqa: E402

logger = logging.getLogger("regen_hero_images")


def _brand_dir() -> Path:
    import os
    return Path(os.environ["BRAND_DIR"]).resolve()


def _wp_id(row: RecipeRow) -> int | None:
    ref = (row.publish_status.get("wp") or {}).get("ref", "")
    return int(ref) if str(ref).isdigit() else None


def _regen_one(row: RecipeRow) -> str:
    from generators.drafter import get_drafter
    from generators.image import generate_image
    from generators.recipe import _seed_by_id
    from publishers.wordpress import set_featured_image

    wp_id = _wp_id(row)
    if wp_id is None:
        return "no-wp-id"
    seed = _seed_by_id(row.id)
    if seed is None:
        return "no-seed"

    voice = get_drafter().draft_voice(seed.title, seed)
    brief = str(voice.get("image_brief") or "").strip()
    if not brief:
        return "no-brief"
    image = generate_image(brief, alt_hint=row.display_name or row.name)
    set_featured_image(wp_id, image, filename=f"hero-{wp_id}.jpg")

    # Mirror to local artifacts so the stored hero matches what's live.
    if image.bytes_:
        dest = _brand_dir() / "data" / "media" / "recipe_artifacts" / row.id / "images" / "featured.jpg"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(image.bytes_)
    logger.info("REGEN %s (wp=%d) provider=%s", row.id, wp_id, image.provider)
    return "regenerated"


def _targets(repo: RecipeRepository, seeds: list[str], limit: int) -> list[RecipeRow]:
    rows = [
        r for r in repo.list_recipes()
        if r.wp_url and _wp_id(r) is not None
        and (not seeds or r.id in seeds)
    ]
    rows.sort(key=lambda r: r.id)
    return rows[:limit] if limit else rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="actually regenerate")
    parser.add_argument("--limit", type=int, default=0, help="cap target count")
    parser.add_argument("--seed", action="append", default=[], help="restrict to ids")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    load_local_env()

    conn = db.connect()
    db.migrate(conn)
    repo = RecipeRepository(conn)
    targets = _targets(repo, args.seed, args.limit)

    if not args.apply:
        logger.info("=== DRY-RUN (no image/WP calls) ===")
        for row in targets:
            logger.info("would regen %-44.44s wp=%s", row.id, _wp_id(row))
        logger.info("targets=%d (run with --apply)", len(targets))
        conn.close()
        return 0

    outcomes: dict[str, str] = {}
    for row in targets:
        try:
            outcomes[row.id] = _regen_one(row)
        except Exception as exc:  # noqa: BLE001 — isolate per-recipe failures
            logger.exception("FAILED %s", row.id)
            outcomes[row.id] = f"error:{type(exc).__name__}"
    conn.close()

    logger.info("=== RESULTS ===")
    for rid, outcome in outcomes.items():
        logger.info("%-44.44s %s", rid, outcome)
    ok = sum(1 for v in outcomes.values() if v == "regenerated")
    logger.info("regenerated=%d total=%d", ok, len(outcomes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
