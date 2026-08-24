"""Topic → carousel slides, caption, and reel on disk.

This is the whole studio pipeline in one call. It reuses the overlay and
reel-composition code that already backs the production publisher rather
than reimplementing it, so what the CLI renders is what the pipeline ships.
"""

from __future__ import annotations

import logging
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from studio.brand import BrandProfile
from studio.fonts import resolve_fonts
from studio.images import generate_slide_image
from studio.plan import CarouselPlan, SlidePlan, plan_carousel

logger = logging.getLogger(__name__)

_RP_ROOT = Path(__file__).resolve().parents[1] / "recipe-publisher"


@dataclass
class RenderResult:
    out_dir: Path
    slide_paths: list[Path] = field(default_factory=list)
    caption_path: Path | None = None
    reel_path: Path | None = None
    providers: list[str] = field(default_factory=list)
    caption: str = ""
    reel_skipped_reason: str = ""


def _slugify(text: str, limit: int = 48) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in text.strip()]
    slug = "".join(keep)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")[:limit] or "carousel"


def _overlay_module() -> Any:
    """Import the overlay renderer, fonts resolved first.

    `text_overlay` reads its font paths from the environment at import time,
    so `resolve_fonts()` has to run before the import, not after.
    """
    resolve_fonts()
    if str(_RP_ROOT) not in sys.path:
        sys.path.insert(0, str(_RP_ROOT))
    from generators import text_overlay

    return text_overlay


def _render_slide(
    spec: SlidePlan, brand: BrandProfile, overlay: Any, *, offline: bool
) -> tuple[bytes, str]:
    img = generate_slide_image(brief=spec.image_brief, query=spec.image_query, offline=offline)
    painted = overlay.apply_overlay(
        img.bytes_,
        overlay.OverlaySpec(headline=spec.headline, subcopy=spec.subcopy),
        headline_y_pct=0.72,
        band_top_pct=0.58,
    )
    if spec.key == "hero":
        painted = overlay.apply_follow_badge(painted, handle=brand.handle)
    if spec.key == "final":
        painted = overlay.apply_site_cta_ribbon(painted, cta_text=brand.cta_ribbon)
    return painted, img.provider


def render_carousel(
    topic: str,
    brand: BrandProfile,
    out_dir: Path,
    *,
    offline: bool = False,
    make_reel: bool = True,
    plan: CarouselPlan | None = None,
) -> RenderResult:
    """Plan, render, and write a full carousel + caption + reel."""
    out_dir = out_dir / _slugify(topic)
    out_dir.mkdir(parents=True, exist_ok=True)
    result = RenderResult(out_dir=out_dir)

    plan = plan or plan_carousel(topic, brand, offline=offline)
    result.caption = plan.caption
    overlay = _overlay_module()

    rendered: list[bytes] = []
    for i, spec in enumerate(plan.slides, start=1):
        logger.info("rendering slide %d/%d (%s)", i, len(plan.slides), spec.key)
        painted, provider = _render_slide(spec, brand, overlay, offline=offline)
        path = out_dir / f"slide-{i}.jpg"
        path.write_bytes(painted)
        result.slide_paths.append(path)
        result.providers.append(provider)
        rendered.append(painted)

    result.caption_path = out_dir / "caption.txt"
    result.caption_path.write_text(plan.caption + "\n", encoding="utf-8")

    if make_reel:
        if shutil.which("ffmpeg") is None:
            result.reel_skipped_reason = "ffmpeg not found on PATH"
            logger.warning("skipping reel — %s", result.reel_skipped_reason)
        else:
            if str(_RP_ROOT) not in sys.path:
                sys.path.insert(0, str(_RP_ROOT))
            from generators.reel import ReelCompositionError, compose_reel

            try:
                result.reel_path = compose_reel(
                    rendered,
                    out_dir / "reel.mp4",
                    slide_duration_s=3.0,
                    transition_duration_s=0.4,
                )
            except (ReelCompositionError, ValueError) as e:
                result.reel_skipped_reason = str(e)
                logger.warning("reel composition failed — %s", e)

    return result
