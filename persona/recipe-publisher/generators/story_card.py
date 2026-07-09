from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

from generators.text_overlay import apply_follow_badge, apply_image_badge, apply_site_cta_ribbon

STORY_W, STORY_H = 1080, 1920
_HEADLINE_FONT_PATH = "/System/Library/Fonts/Supplemental/Arial Black.ttf"
_BODY_FONT_PATH = "/System/Library/Fonts/Helvetica.ttc"
_CREAM = (245, 239, 229)
_AMBER = (255, 180, 60)
_SUMMER_HOOK = "☀️ NEW SUMMER RECIPE"


def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _crop_to_ratio(img: Image.Image, w: int, h: int) -> Image.Image:
    target_w = min(img.width, img.height * w // h)
    target_h = min(img.height, img.width * h // w)
    left = (img.width - target_w) // 2
    top = (img.height - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


def _apply_dark_band(img: Image.Image, top: int, height: int, alpha: int) -> Image.Image:
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle([0, top, img.width, top + height], fill=(0, 0, 0, alpha))
    return Image.alpha_composite(img, overlay)


def _apply_bottom_gradient(img: Image.Image, start_y: int, alpha_max: int) -> Image.Image:
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    h = img.height
    for y in range(start_y, h):
        t = (y - start_y) / max(1, h - start_y)
        a = int(alpha_max * t)
        draw.rectangle([0, y, img.width, y + 1], fill=(0, 0, 0, a))
    return Image.alpha_composite(img, overlay)


def _draw_centered_text(
    img: Image.Image,
    text: str,
    y: int,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: tuple,
    stroke_width: int = 3,
) -> None:
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    x = (STORY_W - text_width) // 2
    draw.text(
        (x, y),
        text,
        font=font,
        fill=(*fill, 255),
        stroke_width=stroke_width,
        stroke_fill=(0, 0, 0, 255),
    )


def compose_story_card(
    hero_image_bytes: bytes, recipe_name: str, wp_url: str, badge_path: str = ""
) -> bytes:
    img = Image.open(io.BytesIO(hero_image_bytes)).convert("RGBA")
    img = _crop_to_ratio(img, 9, 16).resize((STORY_W, STORY_H), Image.Resampling.LANCZOS)

    img = _apply_dark_band(img, top=0, height=int(STORY_H * 0.30), alpha=160)
    img = _apply_bottom_gradient(img, start_y=int(STORY_H * 0.60), alpha_max=190)

    headline_font = _load_font(_HEADLINE_FONT_PATH, int(STORY_W * 0.048))
    _draw_centered_text(img, _SUMMER_HOOK, y=int(STORY_H * 0.16), font=headline_font, fill=_AMBER)

    body_font = _load_font(_BODY_FONT_PATH, int(STORY_W * 0.040))
    _draw_centered_text(
        img, recipe_name, y=int(STORY_H * 0.22), font=body_font, fill=_CREAM, stroke_width=2
    )

    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=90)

    result = buf.getvalue()
    result = apply_follow_badge(result, corner="top_left")
    if badge_path:
        result = apply_image_badge(result, badge_path, corner="top_right", width_pct=0.22)
    result = apply_site_cta_ribbon(result, "NEW RECIPE  →  DOGFOODANDFUN.COM")
    return result
