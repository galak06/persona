"""recipe-publisher main orchestrator.

Replaces the n8n recipes workflow. See SKILL.md for the contract.
Designed to be invoked from launchd via run_with_watchdog.py, or directly via
`claude run recipe-publisher --topic "..."`.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from generators.carousel import generate_carousel_slides
from generators.image import GeneratedImage, generate_image
from generators.recipe import Recipe, generate_recipe
from publishers.instagram import IGPublishResult, publish_carousel_to_instagram
from publishers.wordpress import WPPublishResult, publish_to_wordpress

logger = logging.getLogger("recipe_publisher")

SKILL_DIR = Path(__file__).parent
STATE_DIR = SKILL_DIR / "state"
REPORT_DIR = Path(os.getenv("RECIPE_REPORT_DIR", "/mnt/dogfoodandfun"))


# ---------- typed result envelope ----------


@dataclass
class RunResult:
    started_at: str
    finished_at: str | None = None
    status: str = "in_progress"  # in_progress | success | failed | skipped
    topic: str | None = None
    recipe_title: str | None = None
    wp_post_id: int | None = None
    wp_post_url: str | None = None
    ig_media_id: str | None = None
    ig_permalink: str | None = None
    dry_run: bool = True
    error: str | None = None
    skipped_reason: str | None = None
    warnings: list[str] = field(default_factory=list)


# ---------- state helpers ----------


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        logger.warning("state file %s is corrupt: %s — using default", path, exc)
        return default


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    tmp.replace(path)


def write_last_run(result: RunResult) -> None:
    _atomic_write_json(STATE_DIR / "last_run.json", asdict(result))


def append_published(result: RunResult, recipe: Recipe) -> None:
    if result.status != "success" or result.dry_run:
        return
    path = STATE_DIR / "published_recipes.json"
    history: list[dict[str, Any]] = _load_json(path, default=[])
    history.append(
        {
            "title": recipe.title,
            "slug": recipe.slug,
            "wp_post_id": result.wp_post_id,
            "ig_media_id": result.ig_media_id,
            "published_at": result.finished_at,
        }
    )
    _atomic_write_json(path, history)


# ---------- topic selection + dedup ----------


def pick_topic(cli_topic: str | None) -> str:
    if cli_topic:
        return cli_topic.strip()
    queue: list[dict[str, Any]] = _load_json(STATE_DIR / "ideas_queue.json", default=[])
    if not queue:
        raise RuntimeError(
            "no topic provided and ideas_queue.json is empty — populate state/ideas_queue.json or pass --topic"
        )
    queue_sorted = sorted(queue, key=lambda x: -int(x.get("priority", 0)))
    return queue_sorted[0]["topic"]


def is_duplicate(recipe: Recipe) -> bool:
    history: list[dict[str, Any]] = _load_json(
        STATE_DIR / "published_recipes.json", default=[]
    )
    existing_slugs = {h["slug"] for h in history}
    return recipe.slug in existing_slugs


# ---------- report ----------


def write_report(result: RunResult, recipe: Recipe | None, image: GeneratedImage | None) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = REPORT_DIR / f"recipe-publisher-report-{today}.md"
    lines = [
        f"# recipe-publisher run — {today}",
        "",
        f"- **status:** `{result.status}`",
        f"- **dry_run:** `{result.dry_run}`",
        f"- **topic:** {result.topic or '_(not selected)_'}",
        f"- **started_at:** {result.started_at}",
        f"- **finished_at:** {result.finished_at or '_(unfinished)_'}",
    ]
    if result.error:
        lines += ["", "## ❌ Error", "", f"```\n{result.error}\n```"]
    if result.warnings:
        lines += ["", "## ⚠️ Warnings", *(f"- {w}" for w in result.warnings)]
    if recipe:
        lines += [
            "",
            "## Recipe",
            "",
            f"**Title:** {recipe.title}",
            f"**Slug:** `{recipe.slug}`",
            f"**Meta description:** {recipe.meta_description}",
            "",
            "### Body (markdown)",
            "",
            recipe.body_markdown,
            "",
            "### IG caption",
            "",
            f"```\n{recipe.ig_caption}\n```",
        ]
    if image:
        lines += ["", "## Image", "", f"- **URL:** {image.url}", f"- **Alt:** {image.alt_text}"]
    if result.wp_post_url:
        lines += ["", f"## WP post: {result.wp_post_url}"]
    if result.ig_permalink:
        lines += [f"## IG post: {result.ig_permalink}"]
    path.write_text("\n".join(lines) + "\n")
    return path


def _publish_ig(
    recipe: Recipe,
    wp: WPPublishResult,
    result: RunResult,
) -> IGPublishResult:
    """Always carousel. If the seed has no carousel config, fail the run —
    single-image IG is not an acceptable format for this brand."""
    slides = generate_carousel_slides(
        seed_id=recipe.seed_id,
        recipe_title=recipe.title,
    )
    logger.info("publishing IG carousel (%d slides)", len(slides))
    return publish_carousel_to_instagram(recipe, slides)


# ---------- orchestrator ----------


def run(topic: str | None, dry_run: bool, skip_ig: bool) -> RunResult:
    started = datetime.now(timezone.utc).isoformat()
    result = RunResult(started_at=started, dry_run=dry_run)
    recipe: Recipe | None = None
    image: GeneratedImage | None = None

    try:
        result.topic = pick_topic(topic)
        logger.info("topic selected: %s", result.topic)

        recipe = generate_recipe(result.topic)
        result.recipe_title = recipe.title

        if is_duplicate(recipe):
            result.status = "skipped"
            result.skipped_reason = f"recipe with slug={recipe.slug!r} already published"
            return result

        image = generate_image(recipe.image_brief, alt_hint=recipe.title)

        if dry_run:
            result.status = "success"
            result.warnings.append("dry_run=True — no WP/IG calls made")
            return result

        wp: WPPublishResult = publish_to_wordpress(recipe, image)
        result.wp_post_id = wp.post_id
        result.wp_post_url = wp.permalink
        if wp.warnings:
            result.warnings.extend(wp.warnings)

        if skip_ig:
            result.warnings.append("skip_ig=True — IG step skipped")
        else:
            ig = _publish_ig(recipe, wp, result)
            result.ig_media_id = ig.media_id
            result.ig_permalink = ig.permalink
            if ig.warnings:
                result.warnings.extend(ig.warnings)

        result.status = "success"
    except Exception as exc:  # noqa: BLE001 — top-level boundary, must catch all
        logger.exception("recipe-publisher run failed")
        result.status = "failed"
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        result.finished_at = datetime.now(timezone.utc).isoformat()
        write_last_run(result)
        append_published(result, recipe) if recipe else None
        report = write_report(result, recipe, image)
        logger.info("report written: %s", report)

    return result


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    parser = argparse.ArgumentParser(prog="recipe-publisher")
    parser.add_argument("--topic", default=None, help="explicit recipe topic; else pulled from queue")
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        help="actually publish (default is dry-run for safety)",
    )
    parser.add_argument("--skip-ig", action="store_true", help="publish to WP only")
    args = parser.parse_args(argv)

    result = run(topic=args.topic, dry_run=not args.no_dry_run, skip_ig=args.skip_ig)
    return 0 if result.status in {"success", "skipped"} else 1


if __name__ == "__main__":
    sys.exit(main())
