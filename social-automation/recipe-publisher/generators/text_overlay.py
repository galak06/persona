"""Deterministic text overlay for IG carousel slides.

Diffusion models garble text — we render it with PIL instead. Each call takes
raw image bytes + headline + subcopy and returns new JPEG bytes with a dark
gradient band at the bottom and cream-colored typography above it.

Fonts are Mac system paths; running elsewhere needs a config override.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFilter, ImageFont

_DEFAULT_HEADLINE_FONT = os.getenv(
    "OVERLAY_HEADLINE_FONT",
    "/System/Library/Fonts/Supplemental/Arial Black.ttf",
)
_DEFAULT_SUBCOPY_FONT = os.getenv(
    "OVERLAY_SUBCOPY_FONT",
    "/System/Library/Fonts/Helvetica.ttc",
)
_SUBCOPY_FONT_INDEX = int(os.getenv("OVERLAY_SUBCOPY_FONT_INDEX", "1"))  # 1 = Helvetica Bold

_CREAM = (245, 239, 229)  # #f5efe5


@dataclass
class OverlaySpec:
    headline: str  # supports embedded \n for multi-line
    subcopy: str


def apply_overlay(
    image_bytes: bytes,
    spec: OverlaySpec,
    *,
    output_quality: int = 92,
    headline_y_pct: float = 0.72,
    band_top_pct: float = 0.58,
) -> bytes:
    """Return JPEG bytes of the input image with spec rendered on a dark gradient band.

    `headline_y_pct` sets the vertical center of the headline (default 0.72 for
    1:1). For 9:16 Reels, pass ~0.60 to clear IG's bottom UI overlay zone.
    `band_top_pct` is where the dark gradient begins (default 0.58 — should
    generally be headline_y_pct - 0.14).
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    w, h = img.size

    # Bottom darkness gradient — stronger now: transparent at band_top_pct,
    # ramping to ~88% black at the bottom so headline punches against any image.
    gradient = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(gradient)
    band_top = int(h * band_top_pct)
    for y in range(band_top, h):
        t = (y - band_top) / max(1, h - band_top)
        alpha = int(225 * (t ** 1.2))
        gdraw.rectangle([0, y, w, y + 1], fill=(0, 0, 0, alpha))

    # Mobile-optimized sizes. Reels are watched at ~400-600px wide; the
    # headline must be readable in 1.5s and survive aggressive compression.
    ref = min(w, h)
    head_size = int(ref * 0.10)   # ~108px on 1080-wide
    sub_size = int(ref * 0.052)   # ~56px on 1080-wide
    head_font = ImageFont.truetype(_DEFAULT_HEADLINE_FONT, head_size)
    sub_font = ImageFont.truetype(_DEFAULT_SUBCOPY_FONT, sub_size, index=_SUBCOPY_FONT_INDEX)

    lines = spec.headline.split("\n")
    line_spacing = int(head_size * 1.06)
    total_head_h = line_spacing * len(lines)
    head_y = int(h * headline_y_pct) - total_head_h // 2

    # Soft drop shadow underneath the stroked text for extra punch on any background.
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(shadow)
    for i, line in enumerate(lines):
        bbox = sdraw.textbbox((0, 0), line, font=head_font)
        line_w = bbox[2] - bbox[0]
        x = (w - line_w) // 2
        y = head_y + i * line_spacing
        sdraw.text((x + 3, y + 4), line, font=head_font, fill=(0, 0, 0, 180))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=6))

    out = Image.alpha_composite(img, shadow)
    out = Image.alpha_composite(out, gradient)
    draw = ImageDraw.Draw(out)

    # Headline with stroked outline — guarantees legibility on busy food shots.
    head_stroke = max(3, head_size // 22)
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=head_font)
        line_w = bbox[2] - bbox[0]
        x = (w - line_w) // 2
        y = head_y + i * line_spacing
        draw.text(
            (x, y), line, font=head_font, fill=(*_CREAM, 255),
            stroke_width=head_stroke, stroke_fill=(0, 0, 0, 255),
        )

    sub_y = head_y + total_head_h + int(head_size * 0.35)
    sub_stroke = max(2, sub_size // 28)
    sb = draw.textbbox((0, 0), spec.subcopy, font=sub_font)
    sw = sb[2] - sb[0]
    draw.text(
        ((w - sw) // 2, sub_y), spec.subcopy, font=sub_font,
        fill=(*_CREAM, 245),
        stroke_width=sub_stroke, stroke_fill=(0, 0, 0, 220),
    )

    buf = io.BytesIO()
    out.convert("RGB").save(buf, "JPEG", quality=output_quality)
    return buf.getvalue()


def apply_follow_badge(
    image_bytes: bytes,
    handle: str = "@dogfoodandfun",
    *,
    output_quality: int = 92,
    corner: str = "top_right",
    padding_pct: float = 0.035,
) -> bytes:
    """Add a small rounded-pill follow badge in the chosen corner.

    Used on slide 1 (hero) to brand the account ID into every new viewer's
    first impression. `corner` is one of top_right/top_left/bottom_right/bottom_left.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    w, h = img.size
    ref = min(w, h)
    font_size = int(ref * 0.036)
    font = ImageFont.truetype(_DEFAULT_SUBCOPY_FONT, font_size, index=0)

    measure = ImageDraw.Draw(img)
    bbox = measure.textbbox((0, 0), handle, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    pad_x = int(font_size * 0.85)
    pad_y = int(font_size * 0.45)
    pill_w = tw + pad_x * 2
    pill_h = th + pad_y * 2
    margin = int(ref * padding_pct)

    positions = {
        "top_right": (w - margin - pill_w, margin),
        "top_left": (margin, margin),
        "bottom_right": (w - margin - pill_w, h - margin - pill_h),
        "bottom_left": (margin, h - margin - pill_h),
    }
    x0, y0 = positions.get(corner, positions["top_right"])

    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ldraw = ImageDraw.Draw(layer)
    ldraw.rounded_rectangle(
        [x0, y0, x0 + pill_w, y0 + pill_h],
        radius=pill_h // 2,
        fill=(0, 0, 0, 170),
    )
    ldraw.text(
        (x0 + pad_x, y0 + pad_y - bbox[1]),
        handle,
        font=font,
        fill=(*_CREAM, 255),
    )

    out = Image.alpha_composite(img, layer)
    buf = io.BytesIO()
    out.convert("RGB").save(buf, "JPEG", quality=output_quality)
    return buf.getvalue()


def apply_image_badge(
    image_bytes: bytes,
    badge_path: str,
    *,
    corner: str = "top_right",
    width_pct: float = 0.14,
    margin_pct: float = 0.035,
    output_quality: int = 92,
) -> bytes:
    """Composite a transparent PNG badge (e.g. the Nalla-approved seal) into a corner.

    Used on the carousel POST hero to stamp each recipe with the seal in place
    of the drawn @handle pill. The badge keeps its aspect ratio; ``width_pct`` is
    its width as a fraction of the slide width. ``corner`` is one of
    top_right/top_left/bottom_right/bottom_left. Reels intentionally do NOT get
    this badge \u2014 the post/reel split lives in
    ``carousel.generate_post_and_reel_slides`` (consumed by workers.worker_post_images).
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    w, h = img.size
    badge = Image.open(badge_path).convert("RGBA")
    bw = max(1, int(w * width_pct))
    bh = max(1, int(badge.height * bw / badge.width))
    badge = badge.resize((bw, bh), Image.Resampling.LANCZOS)
    margin = int(min(w, h) * margin_pct)

    positions = {
        "top_right": (w - margin - bw, margin),
        "top_left": (margin, margin),
        "bottom_right": (w - margin - bw, h - margin - bh),
        "bottom_left": (margin, h - margin - bh),
    }
    x0, y0 = positions.get(corner, positions["top_right"])

    img.alpha_composite(badge, (x0, y0))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=output_quality)
    return buf.getvalue()


def apply_site_cta_ribbon(
    image_bytes: bytes,
    cta_text: str = "FULL RECIPE  \u2192  DOGFOODANDFUN.COM",
    *,
    output_quality: int = 92,
    ribbon_pct: float = 0.11,
    ribbon_color: tuple[int, int, int] = (58, 34, 26),
) -> bytes:
    """Paint a solid colored ribbon across the bottom with a bold CTA.

    Used on slide 4 (final) to drive site clicks. The ribbon is opaque and
    sits above any existing bottom-gradient text, so the CTA is the last
    thing a viewer reads before deciding whether to tap the bio link.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    w, h = img.size
    ribbon_h = int(h * ribbon_pct)
    ribbon_top = h - ribbon_h

    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ldraw = ImageDraw.Draw(layer)
    ldraw.rectangle([0, ribbon_top, w, h], fill=(*ribbon_color, 245))

    ref = min(w, h)
    font_size = int(ref * 0.044)
    font = ImageFont.truetype(_DEFAULT_HEADLINE_FONT, font_size)
    bbox = ldraw.textbbox((0, 0), cta_text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    text_x = (w - tw) // 2
    text_y = ribbon_top + (ribbon_h - th) // 2 - bbox[1]
    ldraw.text((text_x, text_y), cta_text, font=font, fill=(*_CREAM, 255))

    out = Image.alpha_composite(img, layer)
    buf = io.BytesIO()
    out.convert("RGB").save(buf, "JPEG", quality=output_quality)
    return buf.getvalue()
