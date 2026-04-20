"""Tests for the conversion-oriented overlays: follow badge + site CTA ribbon.

These verify shape + region-of-change without asserting pixel-perfect output
(PIL rendering varies across platforms).
"""

from __future__ import annotations

import io

from PIL import Image

from generators.text_overlay import apply_follow_badge, apply_site_cta_ribbon


def _solid_jpeg(size: int = 1080, color: tuple[int, int, int] = (200, 200, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, "JPEG", quality=92)
    return buf.getvalue()


def _open(b: bytes) -> Image.Image:
    return Image.open(io.BytesIO(b)).convert("RGB")


def test_follow_badge_preserves_dimensions() -> None:
    src = _solid_jpeg(1080)
    out = apply_follow_badge(src, "@dogfoodandfun")
    assert _open(out).size == (1080, 1080)


def test_follow_badge_modifies_top_right() -> None:
    src = _solid_jpeg(1080, color=(200, 200, 200))
    out = apply_follow_badge(src, "@dogfoodandfun", corner="top_right")
    img = _open(out)
    # Sample a pixel well inside the badge region (top-right corner, ~6% in/down).
    px = img.getpixel((int(1080 * 0.92), int(1080 * 0.05)))
    # Badge is dark-on-light; at least one channel must be well below the 200 baseline.
    assert min(px) < 160, f"expected darkened pixel in top-right, got {px}"


def test_follow_badge_leaves_center_untouched() -> None:
    src = _solid_jpeg(1080, color=(200, 200, 200))
    out = apply_follow_badge(src, "@dogfoodandfun")
    center = _open(out).getpixel((540, 540))
    # JPEG round-trip causes a tiny drift; 190+ proves no overlay was drawn here.
    assert min(center) > 190, f"center should stay light, got {center}"


def test_site_cta_ribbon_paints_bottom_strip() -> None:
    src = _solid_jpeg(1080, color=(200, 200, 200))
    out = apply_site_cta_ribbon(src)
    img = _open(out)
    # Ribbon occupies bottom ~11%. Sample a corner pixel in the ribbon —
    # avoid the centered CTA text so we hit the solid ribbon color, not
    # overlaid cream text.
    ribbon_y = int(1080 * 0.96)
    px = img.getpixel((80, ribbon_y))
    # Default ribbon is dark terracotta-ish; all channels well below the baseline.
    assert max(px) < 120, f"expected dark ribbon pixel, got {px}"


def test_site_cta_ribbon_leaves_top_untouched() -> None:
    src = _solid_jpeg(1080, color=(200, 200, 200))
    out = apply_site_cta_ribbon(src)
    top_px = _open(out).getpixel((540, 100))
    assert min(top_px) > 190, f"top area should stay light, got {top_px}"


def test_site_cta_ribbon_preserves_dimensions() -> None:
    src = _solid_jpeg(1080)
    out = apply_site_cta_ribbon(src)
    assert _open(out).size == (1080, 1080)
