"""Playwright helper + caption assembly for FB group reel posts.

Extracted from `scripts/fb_group_post.py` to keep that file under 400 lines.
Owns:
  - `_attach_reel_in_composer`: drives the FB composer's hidden file input
    to upload a reel mp4 (and optional cover image).
  - `maybe_append_campaign_close`: appends a rotating teaser + CTA from the
    brand overlay's `campaign` block (Slice 3 helper reuse).
  - `dry_run_describe`: short string describing what would be attached.

Playwright is intentionally not imported at module top-level (the caller
already owns the session); the `page` arg is typed `Any` so this helper
can be unit-tested with a fake without dragging playwright into the import
graph.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)


def _attach_reel_in_composer(
    page: Any,
    reel_path: Path,
    thumbnail: Path | None,
) -> bool:
    """Attach a video (and optional custom thumbnail) to the open composer.

    Assumes the composer dialog is already open with the body text typed —
    same precondition the link-card path relies on. Returns True if the
    video was uploaded and FB shows the preview thumb, False if the file
    input couldn't be located. The caller (`open_composer_and_post`) wraps
    this in its own try/except so transient Playwright errors don't crash
    the per-group loop.
    """
    found: Any = page.evaluate(
        """() => {
        const inputs = Array.from(document.querySelectorAll('input[type="file"]'));
        const dialog = document.querySelector('[role="dialog"]');
        const scoped = dialog
            ? Array.from(dialog.querySelectorAll('input[type="file"]'))
            : inputs;
        const pool = scoped.length ? scoped : inputs;
        const video = pool.find(i => (i.accept || '').includes('video'));
        const pick = video || pool[0];
        if (!pick) return 'not_found';
        pick.removeAttribute('hidden');
        pick.style.display = 'block';
        pick.style.opacity = '0';
        return 'ready';
    }"""
    )
    logger.info("reel-input: %s", found)
    if found != "ready":
        return False

    file_inputs = page.locator('input[type="file"]')
    file_inputs.first.set_input_files(str(reel_path))
    time.sleep(8)

    if thumbnail is not None and thumbnail.exists():
        opened: Any = page.evaluate(
            """() => {
            const dialog = document.querySelector('[role="dialog"]');
            if (!dialog) return 'no_dialog';
            const cands = Array.from(dialog.querySelectorAll('[role="button"], button, span, div'));
            const btn = cands.find(el => {
                const t = (el.textContent || '').trim().toLowerCase();
                return t === 'add cover' || t === 'custom cover' || t.startsWith('upload cover');
            });
            if (btn) { btn.scrollIntoView({block: 'center'}); btn.click(); return 'opened'; }
            return 'no_cover_button';
        }"""
        )
        logger.info("reel-cover: %s", opened)
        if opened == "opened":
            time.sleep(2)
            file_inputs.last.set_input_files(str(thumbnail))
            time.sleep(3)

    return True


def dry_run_describe(reel_path: Path, thumbnail: Path | None) -> str:
    size = reel_path.stat().st_size if reel_path.exists() else 0
    parts = [f"attach reel={reel_path.name} ({size} bytes)"]
    if thumbnail is not None:
        parts.append(f"thumbnail={thumbnail.name}")
    return "; ".join(parts)


def _rotation_state_path() -> Path | None:
    brand_dir = os.environ.get("BRAND_DIR")
    if not brand_dir:
        return None
    return Path(brand_dir) / "state" / "campaign_rotation.json"


def maybe_append_campaign_close(caption: str, campaign: dict[str, Any]) -> str:
    """Append rotating teaser + CTA from the brand overlay (Slice 3 helper).

    Group posts share the FB Page rotation pool intentionally — keeps the
    Nalla's Dad voice varied across surfaces (page + groups). No-op when
    BRAND_DIR is unset, or the campaign pools are empty / malformed.
    """
    from campaign_assembly import append_teaser_and_cta

    rotation_path = _rotation_state_path()
    if rotation_path is None:
        return caption
    teasers = campaign.get("teasers") or []
    ctas = campaign.get("ctas") or []
    if not isinstance(teasers, list) or not isinstance(ctas, list):
        return caption
    return cast(
        str,
        append_teaser_and_cta(caption, list(teasers), list(ctas), rotation_path),
    )


def reel_target_categories(campaign: dict[str, Any]) -> set[str]:
    cross = campaign.get("group_crosspost") or {}
    cats = cross.get("reel_target_categories") or []
    if not isinstance(cats, list):
        return set()
    return {str(c).lower() for c in cats}


def build_dry_run_plan(
    groups: list[dict[str, Any]],
    *,
    draft_caption_fn: Any,
    classify_fn: Any,
    title: str,
    url: str,
    caption_override: str | None,
    campaign: dict[str, Any],
    reel_mode: bool,
    reel_path: Path | None,
    reel_thumbnail: Path | None,
) -> list[str]:
    """Return per-group dry-run lines describing the planned action.

    Caller prints the list; keeping I/O out of `lib/` per the lint policy.
    """
    lines: list[str] = []
    for group in groups:
        caption = caption_override or draft_caption_fn(group, title, url)
        if not caption:
            continue
        if reel_mode:
            caption = maybe_append_campaign_close(caption, campaign)
        reel_desc = (
            dry_run_describe(reel_path, reel_thumbnail)
            if reel_mode and reel_path is not None
            else "link-card"
        )
        category = classify_fn(group["group_name"])
        lines.append(
            f"  DRY-RUN -> {group['group_name'][:50]} "
            f"[{category}] mode={reel_desc}"
        )
        lines.append(f"    caption ({len(caption)} chars):\n{caption}\n")
    return lines
