"""Migrate published brand recipes from campaign folders into recipes.db.

For each published recipe campaign folder this:
  1. Validates the recipe is live in WordPress (post id -> slug -> verified
     title search). Recipes NOT found in WP are reported and skipped/removed.
  2. Imports it into recipes.db as a full row (recipe data from seeds.json,
     publish_status from the verified WP post + metadata.json).
  3. Copies artifacts into data/recipe_artifacts/<id>/{images,reels,carousels,audio}.
  4. Moves the original campaign folder to data/_migrated_backup/ (reversible).

Dry-run by default; pass --apply to write. WP credentials and BRAND_DIR are
read from the environment (loaded via lib.local_env) — never inlined.

    python -m scripts.migrate_published_recipes            # dry-run
    python -m scripts.migrate_published_recipes --apply
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, cast

import requests
from recipe_db import db, safety
from recipe_db.models import Ingredient, RecipeRow, RecipeStatus
from recipe_db.repository import RecipeRepository
from requests.auth import HTTPBasicAuth

logger = logging.getLogger("migrate_published_recipes")

_ART_DIRS = {
    "images": {".jpg", ".jpeg", ".png", ".webp", ".gif"},
    "reels": {".mp4", ".mov", ".m4v"},
    "audio": {".mp3", ".wav", ".m4a"},
}
_STOP = {"the", "and", "for", "dog", "dogs", "a", "of", "to", "with", "in"}


def _toks(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", (text or "").lower())) - _STOP


class WP:
    """Minimal read-only WordPress REST client for validation."""

    def __init__(self) -> None:
        self.base = os.environ["WP_URL"].rstrip("/")
        self.auth = HTTPBasicAuth(
            os.environ["WP_USER"], os.environ["WP_APP_PASSWORD"]
        )

    def _get(self, path: str, **params: Any) -> Any:
        r = requests.get(
            f"{self.base}/wp-json/wp/v2/{path}",
            params=params, auth=self.auth, timeout=25,
        )
        return r.json() if r.status_code == 200 else None

    def resolve(
        self, ids: list[Any], slug: str, seed_id: str, seed_title: str
    ) -> dict[str, Any] | None:
        """Return the published WP post for a recipe, or None if not found.

        Tries metadata post ids, then the exact slug, then a title search whose
        candidates are accepted only when their slug OR title shares >=60% of
        the recipe's core words (robust to slugs renamed at publish time).
        """
        for pid in ids:
            if pid:
                p = self._get(
                    f"posts/{pid}",
                    _fields="id,slug,link,status,title,content",
                )
                if isinstance(p, dict) and p.get("status") == "publish":
                    return cast("dict[str, Any]", p)
        if slug:
            hits = self._get(
                "posts", slug=slug, status="publish",
                _fields="id,slug,link,title,content",
            )
            if hits:
                return cast("dict[str, Any]", hits[0])
        id_toks = _toks(seed_id.replace("-", " "))
        title_toks = _toks(seed_title)
        candidates: dict[int, dict[str, Any]] = {}
        for query in (seed_id.replace("-", " "), seed_title):
            for p in self._get(
                "posts", search=query, status="publish", per_page=8,
                _fields="id,slug,link,title,content",
            ) or []:
                candidates[p["id"]] = p
        for p in candidates.values():
            pslug = _toks((p.get("slug") or "").replace("-", " "))
            ptitle = _toks((p.get("title") or {}).get("rendered", ""))
            slug_ok = bool(id_toks) and len(id_toks & pslug) / len(id_toks) >= 0.6
            title_ok = (
                bool(title_toks)
                and len(title_toks & ptitle) / len(title_toks) >= 0.6
            )
            if slug_ok or title_ok:
                return p
        return None


def _has_pdf(post: dict[str, Any]) -> bool:
    html = ((post.get("content") or {}).get("rendered", "")).lower()
    return ".pdf" in html or "recipe-card" in html


def _channel(pub: bool, url: str, ref: str, at: str) -> dict[str, str]:
    return {
        "state": "published" if pub else "",
        "url": url if pub else "",
        "ref": str(ref) if pub else "",
        "at": at if pub else "",
    }


def _publish_status(
    post: dict[str, Any], pdf: bool, meta: dict[str, Any]
) -> dict[str, dict[str, str]]:
    at = str(meta.get("published_at") or "")
    link = post.get("link", "")
    pid = post.get("id", "")
    ig_reel = str(meta.get("ig_reel_permalink") or "")
    ig_pub = bool(meta.get("ig_reel_media_id") or ig_reel)
    fb_pub = bool(meta.get("fb_page_post_id") or meta.get("fb_page_post_permalink"))
    ig = _channel(ig_pub, ig_reel, str(meta.get("ig_reel_media_id") or ""), at)
    ig["caption"] = str(meta.get("ig_caption") or "")
    ig["reel_url"] = ig_reel
    ig["post_url"] = str(meta.get("ig_post_permalink") or "")
    return {
        "wp": _channel(True, link, pid, at),
        "pdf": _channel(pdf, link, pid, at),
        "ig": ig,
        "fb": _channel(
            fb_pub, str(meta.get("fb_page_post_permalink") or ""),
            str(meta.get("fb_page_post_id") or ""), at,
        ),
    }


def _seed_to_row(
    seed: dict[str, Any], post: dict[str, Any],
    status: dict[str, dict[str, str]],
) -> RecipeRow:
    ingredients = [Ingredient(item=line) for line in seed["ingredients"]]
    flags, dog_safe = safety.scan_ingredients(ingredients)
    name = seed["title"]
    digest = hashlib.sha256(
        (seed["id"] + "|" + name).encode("utf-8")
    ).hexdigest()
    return RecipeRow(
        name=name,
        ingredients=ingredients,
        steps=list(seed["steps"]),
        prep_minutes=int(seed.get("prep_minutes", 0)),
        cook_minutes=int(seed.get("cook_minutes", 0)),
        servings=str(seed.get("yield_servings", "")),
        category=seed.get("category", ""),
        tags=list(seed.get("tags", [])),
        source_url=post.get("link", ""),
        source_name="dogfoodandfun.com",
        content_hash=digest,
        id=seed["id"],
        artifacts_path=f"data/recipe_artifacts/{seed['id']}",
        status=RecipeStatus.SEED_EXPORTED,
        toxic_flags=flags,
        dog_safe=dog_safe,
        publish_status=status,
    )


def _copy_artifacts(folder: Path, dest_root: Path, apply: bool) -> int:
    copied = 0
    for src in folder.rglob("*"):
        if not src.is_file() or src.name == ".DS_Store":
            continue
        ext = src.suffix.lower()
        bucket = next(
            (b for b, exts in _ART_DIRS.items() if ext in exts), None
        )
        if bucket is None:
            bucket = "carousels" if "carousel" in src.name.lower() else "meta"
        dest = dest_root / bucket / src.name
        if apply:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        copied += 1
    return copied


def _discover(brand: Path) -> dict[str, tuple[Path, dict[str, Any]]]:
    folders: dict[str, tuple[Path, dict[str, Any]]] = {}
    metas = list((brand / "campaigns").rglob("metadata.json"))
    for meta in metas:
        if "/ready/" in str(meta) or "/in_review/" in str(meta):
            continue  # never touch unpublished work
        if "published" not in str(meta):
            continue
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sid = m.get("seed_id")
        if not isinstance(sid, str) or not sid:
            continue
        prev = folders.get(sid)
        if prev is None or (m.get("wp_live_url") and not prev[1].get("wp_live_url")):
            folders[sid] = (meta.parent, m)
    return folders


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write changes")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    brand = Path(os.environ["BRAND_DIR"]).resolve()
    seeds = {
        s["id"]: s
        for s in json.loads(
            (Path(__file__).parent.parent / "seeds" / "seeds.json").read_text()
        )["seeds"]
    }
    backup_root = brand / "data" / "_migrated_backup"
    art_root = brand / "data" / "recipe_artifacts"
    wp = WP()

    conn = db.connect()
    db.migrate(conn)
    repo = RecipeRepository(conn)

    imported, skipped, removed = [], [], []
    for sid, (folder, meta) in sorted(_discover(brand).items()):
        seed = seeds.get(sid)
        if not seed or not seed.get("ingredients"):
            skipped.append((sid, "not a recipe (no seed/ingredients)"))
            continue
        slug = meta.get("slug") or (
            meta.get("wp_live_url", "").rstrip("/").split("/")[-1]
        )
        post = wp.resolve(
            [meta.get("wp_post_id"), meta.get("wp_draft_id")],
            slug, sid, seed["title"],
        )
        if post is None:
            removed.append((sid, str(folder.relative_to(brand))))
            if args.apply:
                shutil.move(str(folder), str(backup_root / "not_in_wp" / sid))
            continue
        status = _publish_status(post, _has_pdf(post), meta)
        row = _seed_to_row(seed, post, status)
        n_art = _copy_artifacts(folder, art_root / sid, args.apply)
        if args.apply:
            repo.upsert_recipe(row)
            backup_root.mkdir(parents=True, exist_ok=True)
            shutil.move(str(folder), str(backup_root / sid))
        imported.append((sid, post.get("slug"), n_art, status["pdf"]["state"]))

    conn.close()
    mode = "APPLIED" if args.apply else "DRY-RUN"
    logger.info("=== %s ===", mode)
    for sid, slug, n_art, pdf in imported:
        logger.info(
            "import %-38.38s wp=%s pdf=%s artifacts=%d", sid, slug,
            pdf or "-", n_art,
        )
    for sid, why in skipped:
        logger.info("skip   %-38.38s (%s)", sid, why)
    for sid, where in removed:
        logger.info("REMOVE %-38.38s not in WP (%s)", sid, where)
    logger.info(
        "imported=%d skipped=%d removed=%d", len(imported), len(skipped),
        len(removed),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
