"""Tests for the conversion-oriented overlays: follow badge, PNG seal badge,
and site CTA ribbon.

These verify shape + region-of-change without asserting pixel-perfect output
(PIL rendering varies across platforms).
"""

from __future__ import annotations

import io
from pathlib import Path

from generators.text_overlay import (
    apply_follow_badge,
    apply_image_badge,
    apply_site_cta_ribbon,
)
from PIL import Image


def _solid_jpeg(size: int = 1080, color: tuple[int, int, int] = (200, 200, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buf, "JPEG", quality=92)
    return buf.getvalue()


def _open(b: bytes) -> Image.Image:
    return Image.open(io.BytesIO(b)).convert("RGB")


def _px(img: Image.Image, xy: tuple[int, int]) -> tuple[int, ...]:
    """getpixel() typed as a channel tuple (RGB image → 3-tuple)."""
    value = img.getpixel(xy)
    assert isinstance(value, tuple), f"expected channel tuple, got {value!r}"
    return value


def _badge_png(tmp_path: Path, size: int = 256, color: tuple[int, int, int] = (255, 0, 0)) -> str:
    """Opaque colored square PNG with alpha — a stand-in for the Nalla seal."""
    p = tmp_path / "badge.png"
    Image.new("RGBA", (size, size), (*color, 255)).save(p, "PNG")
    return str(p)


def test_follow_badge_preserves_dimensions() -> None:
    src = _solid_jpeg(1080)
    out = apply_follow_badge(src, "@persona")
    assert _open(out).size == (1080, 1080)


def test_follow_badge_modifies_top_right() -> None:
    src = _solid_jpeg(1080, color=(200, 200, 200))
    out = apply_follow_badge(src, "@persona", corner="top_right")
    img = _open(out)
    # Sample a pixel well inside the badge region (top-right corner, ~6% in/down).
    px = _px(img, (int(1080 * 0.92), int(1080 * 0.05)))
    # Badge is dark-on-light; at least one channel must be well below the 200 baseline.
    assert min(px) < 160, f"expected darkened pixel in top-right, got {px}"


def test_follow_badge_leaves_center_untouched() -> None:
    src = _solid_jpeg(1080, color=(200, 200, 200))
    out = apply_follow_badge(src, "@persona")
    center = _px(_open(out), (540, 540))
    # JPEG round-trip causes a tiny drift; 190+ proves no overlay was drawn here.
    assert min(center) > 190, f"center should stay light, got {center}"


def test_image_badge_preserves_dimensions(tmp_path: Path) -> None:
    src = _solid_jpeg(1080)
    out = apply_image_badge(src, _badge_png(tmp_path))
    assert _open(out).size == (1080, 1080)


def test_image_badge_stamps_top_right(tmp_path: Path) -> None:
    src = _solid_jpeg(1080, color=(200, 200, 200))
    out = apply_image_badge(src, _badge_png(tmp_path, color=(255, 0, 0)), corner="top_right")
    img = _open(out)
    # Badge default width is 14%; sample a pixel inside it near the top-right.
    px = _px(img, (int(1080 * 0.93), int(1080 * 0.05)))
    # The red seal must dominate: red high, green/blue low.
    assert px[0] > 150 and px[1] < 100 and px[2] < 100, f"expected red seal pixel, got {px}"


def test_image_badge_leaves_center_untouched(tmp_path: Path) -> None:
    src = _solid_jpeg(1080, color=(200, 200, 200))
    out = apply_image_badge(src, _badge_png(tmp_path), width_pct=0.14)
    center = _px(_open(out), (540, 540))
    assert min(center) > 190, f"center should stay light, got {center}"


def test_image_badge_respects_corner(tmp_path: Path) -> None:
    src = _solid_jpeg(1080, color=(200, 200, 200))
    out = apply_image_badge(src, _badge_png(tmp_path, color=(255, 0, 0)), corner="bottom_left")
    img = _open(out)
    bottom_left = _px(img, (int(1080 * 0.05), int(1080 * 0.95)))
    top_right = _px(img, (int(1080 * 0.95), int(1080 * 0.05)))
    assert bottom_left[0] > 150 and bottom_left[1] < 100, f"badge missing bottom-left: {bottom_left}"
    assert min(top_right) > 190, f"top-right should stay clean, got {top_right}"


def test_site_cta_ribbon_paints_bottom_strip() -> None:
    src = _solid_jpeg(1080, color=(200, 200, 200))
    out = apply_site_cta_ribbon(src)
    img = _open(out)
    # Ribbon occupies bottom ~11%. Sample near the ribbon's bottom edge, below
    # the vertically-centered CTA text — otherwise wide fonts can push the cream
    # text out to the edges and we'd sample text instead of the solid ribbon.
    ribbon_y = int(1080 * 0.995)
    px = _px(img, (80, ribbon_y))
    # Default ribbon is dark terracotta-ish; all channels well below the baseline.
    assert max(px) < 120, f"expected dark ribbon pixel, got {px}"


def test_site_cta_ribbon_leaves_top_untouched() -> None:
    src = _solid_jpeg(1080, color=(200, 200, 200))
    out = apply_site_cta_ribbon(src)
    top_px = _px(_open(out), (540, 100))
    assert min(top_px) > 190, f"top area should stay light, got {top_px}"


def test_site_cta_ribbon_preserves_dimensions() -> None:
    src = _solid_jpeg(1080)
    out = apply_site_cta_ribbon(src)
    assert _open(out).size == (1080, 1080)
