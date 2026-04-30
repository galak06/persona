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
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from generators.carousel import generate_carousel_slides
from generators.image import GeneratedImage, generate_image
from generators.recipe import Recipe, generate_recipe
from publishers.instagram import (
    IGPublishResult,
    post_first_comment_to_instagram,
    publish_carousel_to_instagram,
)
from publishers.pinterest import PinterestPublishResult, publish_pins_for_recipe
from publishers.wordpress import WPPublishResult, publish_to_wordpress
from run_report import write_report

logger = logging.getLogger("recipe_publisher")

SKILL_DIR = Path(__file__).parent
STATE_DIR = SKILL_DIR / "state"


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
    pinterest_pin_ids: list[str] = field(default_factory=list)
    pinterest_permalinks: list[str] = field(default_factory=list)
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
            "pinterest_pin_ids": result.pinterest_pin_ids,
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


def _publish_ig(recipe: Recipe, slides: list[GeneratedImage]) -> IGPublishResult:
    """Always carousel. If the seed has no carousel config, fail the run —
    single-image IG is not an acceptable format for this brand."""
    logger.info("publishing IG carousel (%d slides)", len(slides))
    result = publish_carousel_to_instagram(recipe, slides)

    # Auto-drop a first-comment CTA on our own post. Amplifies the caption's
    # "Comment KEYWORD" CTA and puts a second question above-the-fold in the
    # comments section — comments + saves are the top-ranked engagement signals
    # on IG since 2024, and the first hour after publish is when they count most.
    try:
        msg = _build_first_comment(recipe)
        cid = post_first_comment_to_instagram(result.media_id, msg)
        result.first_comment_id = cid
        logger.info("first-comment posted media=%s comment=%s", result.media_id, cid)
    except Exception as exc:  # noqa: BLE001 — cosmetic, never fail a run over it
        logger.warning("first-comment post failed: %s", exc)
        result.warnings.append(f"first_comment_failed: {exc}")

    return result


_CTA_KEYWORD_RE = re.compile(r"\bComment ([A-Z]{3,})\b")


def _build_first_comment(recipe: Recipe) -> str:
    """Short pinned-style comment that reinforces the caption CTA + adds a question.

    Extracts the caption's "Comment KEYWORD" gated CTA (validated to exist by
    generators.recipe._validate) and builds a two-beat reply: nudge the keyword,
    then ask a low-friction follow-up. Keyword pivot makes it recipe-specific
    without needing a new LLM voice field.
    """
    m = _CTA_KEYWORD_RE.search(recipe.ig_caption)
    keyword = m.group(1) if m else "RECIPE"
    return (
        f"👇 Drop a '{keyword}' and I'll DM you the printable card. "
        f"Or tell me — what's your pup's all-time favorite homemade treat?"
    )


def _publish_pinterest(
    recipe: Recipe,
    slides: list[GeneratedImage],
    wp_post_url: str,
    slide_urls: list[str],
) -> PinterestPublishResult:
    """4 Pins per recipe (one per slide), all linking to the WP post URL.

    Reuses the slide URLs already uploaded to WP by the IG step so we don't
    double-upload. If those URLs aren't available (e.g. skip_ig=True), the
    publisher falls back to uploading them itself.
    """
    logger.info("publishing %d Pinterest pins", len(slides))
    return publish_pins_for_recipe(
        recipe,
        slides,
        wp_post_url=wp_post_url,
        slide_urls=slide_urls or None,
    )


# ---------- orchestrator ----------


def run(
    topic: str | None,
    dry_run: bool,
    skip_ig: bool,
    skip_pinterest: bool = False,
) -> RunResult:
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
            result.warnings.append("dry_run=True — no WP/IG/Pinterest calls made")
            return result

        wp: WPPublishResult = publish_to_wordpress(recipe, image)
        result.wp_post_id = wp.post_id
        result.wp_post_url = wp.permalink
        if wp.warnings:
            result.warnings.extend(wp.warnings)

        slides: list[GeneratedImage] = []
        slide_urls: list[str] = []
        if not skip_ig or not skip_pinterest:
            slides = generate_carousel_slides(
                seed_id=recipe.seed_id,
                recipe_title=recipe.title,
            )

        if skip_ig:
            result.warnings.append("skip_ig=True — IG step skipped")
        else:
            ig = _publish_ig(recipe, slides)
            result.ig_media_id = ig.media_id
            result.ig_permalink = ig.permalink
            slide_urls = ig.image_urls
            if ig.warnings:
                result.warnings.extend(ig.warnings)

        if skip_pinterest:
            result.warnings.append("skip_pinterest=True — Pinterest step skipped")
        elif not result.wp_post_url:
            result.warnings.append(
                "no wp_post_url — Pinterest step skipped (nothing to link to)"
            )
        else:
            try:
                pin = _publish_pinterest(recipe, slides, result.wp_post_url, slide_urls)
                result.pinterest_pin_ids = [p.pin_id for p in pin.pins]
                result.pinterest_permalinks = [p.permalink for p in pin.pins]
                if pin.warnings:
                    result.warnings.extend(pin.warnings)
            except Exception as pin_exc:  # noqa: BLE001 — Pinterest failure shouldn't fail the whole run
                logger.exception("pinterest publish failed")
                result.warnings.append(f"pinterest_failed: {pin_exc}")

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
    parser.add_argument(
        "--skip-pinterest",
        action="store_true",
        help="skip Pinterest step (default: publish 4 pins per recipe)",
    )
    args = parser.parse_args(argv)

    result = run(
        topic=args.topic,
        dry_run=not args.no_dry_run,
        skip_ig=args.skip_ig,
        skip_pinterest=args.skip_pinterest,
    )
    return 0 if result.status in {"success", "skipped"} else 1


if __name__ == "__main__":
    sys.exit(main())
