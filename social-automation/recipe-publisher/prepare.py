"""Recipe prep stage — produces a WP draft + all social assets, no live publish.

Output: <BRAND_DIR>/campaigns/recipes/ready/<seed-id>/
    metadata.json        — wp_draft_id, slug, title, captions, tags
    featured.jpg         — main image (also set as WP featured_media)
    slides/slide_N.jpg   — carousel slides for IG (only if carousel JSON exists)
    recipe_body.html     — final WP body w/ affiliate block (for preview)
    ig_caption.txt       — IG caption (verbatim)
    fb_caption.txt       — FB caption (verbatim)
    status.json          — state machine (awaiting_audio → verified → published)

Audio is owned by the operator — they generate it in an external tool (Suno
etc.) and drop `audio.mp3` into this folder. We don't author music prompts
or lyrics here.

The cron drainer (scripts/publish_prepared.py) reads this folder, promotes
the WP draft to publish, pushes IG carousel + FB post, then moves the folder
to <BRAND_DIR>/campaigns/recipes/published/<seed-id>/.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from campaign_assembly import append_teaser_and_cta
from generators.carousel import generate_carousel_slides
from generators.carousel_drafter import ensure_carousel_json
from generators.image import GeneratedImage, generate_image
from generators.lyrics_drafter import draft_lyrics, render_lyrics_md
from generators.recipe import Recipe, generate_recipe
from generators.reel import ReelCompositionError, compose_reel
from generators.seeds import load_seeds
from generators.step_images import inject_step_images
from publishers.wordpress import (
    WPPublishResult,
    publish_to_wordpress,
    upload_image_to_media_library,
)

# Reuse carousel slides as in-recipe step images: one image under every Nth
# numbered instruction step. 0 disables (slides stay social-only — their
# marketing overlays don't belong in the WP recipe body). Hero stays separate.
_STEP_IMAGE_EVERY_N = 0

logger = logging.getLogger("recipe_publisher.prepare")

SKILL_DIR = Path(__file__).parent
STATE_DIR = SKILL_DIR / "state"
PROJECT_ROOT = SKILL_DIR.parent.parent


def _prepared_root() -> Path:
    """Directory the cron drainer (publish_prepared.py) reads from.

    Must match ``settings.paths.campaigns_dir / "recipes" / "ready"`` in
    scripts/publish_prepared.py, i.e. ``<BRAND_DIR>/campaigns/recipes/ready``.
    Resolved from the ``BRAND_DIR`` env (the recipe-publisher convention);
    falls back to the repo-root campaigns layout when unset.
    """
    brand_dir = os.environ.get("BRAND_DIR")
    base = Path(brand_dir) if brand_dir else PROJECT_ROOT
    return base / "campaigns" / "recipes" / "ready"


PREPARED_ROOT = _prepared_root()


def _load_brand_campaign() -> dict[str, Any] | None:
    """Read `<BRAND_DIR>/brand.json` and return its `campaign` block, or None.

    Mirrors the lookup pattern in `lib/local_env.get_runtime_headless`. Silent
    on missing/malformed input so brands without a campaign block (Slice 1
    not opted in) skip teaser/CTA append without crashing prepare.
    """
    brand_dir = os.environ.get("BRAND_DIR")
    if not brand_dir:
        return None
    brand_path = Path(brand_dir) / "brand.json"
    if not brand_path.exists():
        return None
    try:
        data: Any = json.loads(brand_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    campaign = data.get("campaign")
    return campaign if isinstance(campaign, dict) else None


def _rotation_state_path() -> Path | None:
    brand_dir = os.environ.get("BRAND_DIR")
    if not brand_dir:
        return None
    return Path(brand_dir) / "state" / "campaign_rotation.json"


def _maybe_append_campaign_close(fb_caption: str) -> str:
    """FB-only: append rotating teaser + CTA when the brand has opted in."""
    campaign = _load_brand_campaign()
    rotation_path = _rotation_state_path()
    if campaign is None or rotation_path is None:
        return fb_caption
    teasers = campaign.get("teasers") or []
    ctas = campaign.get("ctas") or []
    if not isinstance(teasers, list) or not isinstance(ctas, list):
        return fb_caption
    return append_teaser_and_cta(fb_caption, list(teasers), list(ctas), rotation_path)


_BULLET_RE = re.compile(r"^\s*•.*$", re.MULTILINE)
_HASHTAG_LINE_RE = re.compile(r"^[\s#].*#\w+.*$", re.MULTILINE)
_BIO_FALLBACK_RE = re.compile(r"^.*link in bio.*$", re.IGNORECASE | re.MULTILINE)


def _fb_caption_from_ig(ig_caption: str) -> str:
    """Derive an FB-adapted post body from the IG caption.

    Keeps: hook (first line), comment CTA line, engagement question.
    Strips: bullet lines, bio-link fallback, hashtag block.
    Replaces "I'll DM you" with "I'll send you" for natural FB phrasing.
    """
    text = ig_caption.strip()
    text = _BULLET_RE.sub("", text)
    text = _BIO_FALLBACK_RE.sub("", text)
    text = _HASHTAG_LINE_RE.sub("", text)
    text = text.replace("I'll DM you", "I'll send you")
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    return "\n\n".join(lines).strip()


@dataclass
class PrepareResult:
    started_at: str
    finished_at: str | None = None
    status: str = "in_progress"  # in_progress | success | failed | skipped
    topic: str | None = None
    seed_id: str | None = None
    slug: str | None = None
    wp_draft_id: int | None = None
    wp_admin_url: str | None = None
    folder: str | None = None
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    tmp.replace(path)


def _is_already_prepared(seed_id: str) -> Path | None:
    candidate = PREPARED_ROOT / seed_id
    if candidate.exists() and (candidate / "status.json").exists():
        return candidate
    return None


def _save_image(image: GeneratedImage, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(image.bytes_)


def _save_carousel(slides: list[GeneratedImage], folder: Path) -> list[str]:
    folder.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for i, slide in enumerate(slides, 1):
        ext = "jpg" if (slide.content_type or "image/jpeg").endswith("jpeg") else "png"
        p = folder / f"slide_{i}.{ext}"
        p.write_bytes(slide.bytes_)
        paths.append(str(p.relative_to(folder.parent)))
    return paths


def _build_metadata(
    recipe: Recipe,
    wp: WPPublishResult,
    slide_paths: list[str],
) -> dict[str, Any]:
    return {
        "seed_id": recipe.seed_id,
        "slug": recipe.slug,
        "title": recipe.title,
        "topic_keywords": list(getattr(recipe, "topic_keywords", []) or []),
        "tags": list(recipe.tags),
        "wp_draft_id": wp.post_id,
        "wp_draft_preview_url": wp.permalink,
        "wp_admin_url": f"https://dogfoodandfun.com/wp-admin/post.php?post={wp.post_id}&action=edit",
        "featured_image_url": wp.featured_image_url,
        "ig_caption": recipe.ig_caption,
        "fb_caption": getattr(recipe, "fb_caption", "") or "",
        "carousel_slides": slide_paths,
        "prepared_at": datetime.now(timezone.utc).isoformat(),
    }


def _write_initial_status(folder: Path) -> None:
    _atomic_write_json(
        folder / "status.json",
        {
            "state": "awaiting_audio",
            "history": [
                {
                    "at": datetime.now(timezone.utc).isoformat(),
                    "to": "awaiting_audio",
                    "by": "prepare",
                }
            ],
        },
    )


def prepare(topic: str, *, force: bool = False) -> PrepareResult:
    """End-to-end recipe prep — generates artifacts + WP draft, no IG/FB push."""
    started = datetime.now(timezone.utc).isoformat()
    result = PrepareResult(started_at=started, topic=topic)

    try:
        campaign = _load_brand_campaign() or {}
        hook_blocklist = campaign.get("hook_blocklist")
        recipe = generate_recipe(topic, hook_blocklist=hook_blocklist)
        result.seed_id = recipe.seed_id
        result.slug = recipe.slug
        result.recipe_title = recipe.title  # type: ignore[attr-defined]

        existing = _is_already_prepared(recipe.seed_id)
        if existing and not force:
            result.status = "skipped"
            result.folder = str(existing)
            result.warnings.append(
                f"already prepared at {existing} — pass --force to regenerate"
            )
            return result

        folder = PREPARED_ROOT / recipe.seed_id
        if existing and force:
            shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, exist_ok=True)
        result.folder = str(folder)

        image = generate_image(recipe.image_brief, alt_hint=recipe.title)
        _save_image(image, folder / "featured.jpg")

        # Carousel JSON: auto-draft via Gemini if missing. The operator can
        # review/edit seeds/carousels/<id>.json afterward and re-run prep
        # with --force to regenerate slides + reel from the edited brief.
        seed_obj = next((s for s in load_seeds() if s.id == recipe.seed_id), None)
        slide_paths: list[str] = []
        slide_bytes_list: list[bytes] = []
        if seed_obj is not None:
            try:
                ensure_carousel_json(seed_obj, force=force)
                slides = generate_carousel_slides(
                    seed_id=recipe.seed_id, recipe_title=recipe.title
                )
                slide_paths = _save_carousel(slides, folder / "slides")
                slide_bytes_list = [s.bytes_ or b"" for s in slides]
            except Exception as exc:  # noqa: BLE001 — carousel is best-effort
                logger.warning("carousel slides skipped for %s: %s", recipe.seed_id, exc)
                result.warnings.append(f"carousel/slides skipped: {exc}")
        else:
            result.warnings.append(
                f"seed {recipe.seed_id} not found in seeds.json — skipping carousel"
            )

        # Silent reel video — composes slides into 9:16 mp4, no audio. The
        # operator drops audio.mp3 later; the cron-publish step will mux
        # source.mp4 + audio.mp3 → muxed.mp4 before pushing to IG/FB.
        if slide_bytes_list:
            try:
                compose_reel(slide_bytes_list, folder / "source.mp4", audio_path=None)
            except ReelCompositionError as exc:
                logger.warning("silent reel composition failed for %s: %s", recipe.seed_id, exc)
                result.warnings.append(f"reel skipped: {exc}")

        # Lyrics — Gemini drafts a recipe-grounded lyric, operator reviews
        # and feeds into Suno/etc to generate audio.mp3.
        if seed_obj is not None:
            try:
                lyrics_body = draft_lyrics(seed_obj)
                (folder / "lyrics.md").write_text(
                    render_lyrics_md(seed_obj, lyrics_body),
                    encoding="utf-8",
                )
            except Exception as exc:  # noqa: BLE001 — lyrics are best-effort
                logger.warning("lyrics draft failed for %s: %s", recipe.seed_id, exc)
                result.warnings.append(f"lyrics draft skipped: {exc}")

        # Reuse the carousel slides as in-recipe step images, auto-placed under
        # every Nth instruction step. Best-effort — a failure here must not
        # block the WP draft. Only upload as many slides as steps will use.
        if slides and _STEP_IMAGE_EVERY_N >= 1:
            try:
                needed = len(recipe.steps) // _STEP_IMAGE_EVERY_N
                step_urls: list[str] = []
                for idx, slide in enumerate(slides[:needed], 1):
                    _, src = upload_image_to_media_library(
                        slide, filename=f"{recipe.slug}-step-{idx}.jpg"
                    )
                    step_urls.append(src)
                recipe.body_markdown = inject_step_images(
                    recipe.body_markdown, step_urls,
                    every_n=_STEP_IMAGE_EVERY_N,
                )
            except Exception as exc:  # noqa: BLE001 — step images best-effort
                logger.warning(
                    "step images skipped for %s: %s", recipe.seed_id, exc
                )
                result.warnings.append(f"step images skipped: {exc}")

        wp = publish_to_wordpress(recipe, image, status="draft")
        result.wp_draft_id = wp.post_id
        result.wp_admin_url = (
            f"https://dogfoodandfun.com/wp-admin/post.php?post={wp.post_id}&action=edit"
        )
        if wp.warnings:
            result.warnings.extend(wp.warnings)

        # Save reference artifacts
        (folder / "ig_caption.txt").write_text(recipe.ig_caption, encoding="utf-8")
        fb = getattr(recipe, "fb_caption", "") or _fb_caption_from_ig(recipe.ig_caption)
        fb = _maybe_append_campaign_close(fb)
        (folder / "fb_caption.txt").write_text(fb, encoding="utf-8")
        fb_dm_reply = (
            f"Woof! Thanks for commenting. \U0001f43e Here's your printable recipe card"
            f" for the {recipe.title}:\n"
            f"\U0001f449 {wp.permalink}\n\n"
            f"(Just look for the download button on the page!) Happy cooking for your pup! \U0001f955\U0001f436"
        )
        (folder / "fb_dm_reply.txt").write_text(fb_dm_reply, encoding="utf-8")

        # Inject song teaser when a cooking-song MP3 exists for this recipe
        _song_mp3 = folder / f"{folder.name}.mp3"
        if not _song_mp3.exists():
            # Also check slides/ subfolder
            _song_candidates = list((folder / "slides").glob("*.mp3")) if (folder / "slides").exists() else []
            _song_mp3 = _song_candidates[0] if _song_candidates else None

        if _song_mp3:
            _SONG_TEASER = "\n🎵 I made a full cooking song for this one — come listen on the recipe page."

            # Append to ig_caption.txt — insert BEFORE the hashtag block
            ig_path = folder / "ig_caption.txt"
            ig_text = ig_path.read_text(encoding="utf-8")
            # Find where hashtags start (line beginning with #)
            ig_lines = ig_text.splitlines()
            hashtag_idx = next((i for i, l in enumerate(ig_lines) if l.strip().startswith("#")), None)
            if hashtag_idx is not None:
                ig_lines.insert(hashtag_idx, _SONG_TEASER.strip())
                ig_lines.insert(hashtag_idx, "")  # blank line before teaser
            else:
                ig_lines.append(_SONG_TEASER)
            ig_path.write_text("\n".join(ig_lines), encoding="utf-8")

            # Append to fb_caption.txt
            fb_path = folder / "fb_caption.txt"
            fb_text = fb_path.read_text(encoding="utf-8")
            fb_path.write_text(fb_text.rstrip() + _SONG_TEASER + "\n", encoding="utf-8")

        (folder / "recipe_body.html").write_text(recipe.body_markdown, encoding="utf-8")

        _atomic_write_json(folder / "metadata.json", _build_metadata(recipe, wp, slide_paths))
        _write_initial_status(folder)

        result.status = "success"
    except Exception as exc:  # noqa: BLE001 — top-level boundary
        logger.exception("prepare failed")
        result.status = "failed"
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        result.finished_at = datetime.now(timezone.utc).isoformat()
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(STATE_DIR / "last_prepare.json", asdict(result))

    return result
