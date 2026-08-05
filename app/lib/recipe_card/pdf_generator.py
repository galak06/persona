"""
Generate a "Parchment & Paws" recipe card PDF using reportlab.
Landscape Letter, hand-drawn Caveat font, aged parchment background.
Falls back to Helvetica if Caveat fonts are not installed.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, LETTER
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Font registration                                                    #
# ------------------------------------------------------------------ #
_FONTS_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "fonts"
_CAVEAT_REGULAR = _FONTS_DIR / "Caveat-Regular.ttf"
_CAVEAT_BOLD = _FONTS_DIR / "Caveat-Bold.ttf"

_FONT_REGULAR = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"

def _register_fonts() -> None:
    global _FONT_REGULAR, _FONT_BOLD
    try:
        if _CAVEAT_REGULAR.exists() and _CAVEAT_BOLD.exists():
            pdfmetrics.registerFont(TTFont("Caveat", str(_CAVEAT_REGULAR)))
            pdfmetrics.registerFont(TTFont("Caveat-Bold", str(_CAVEAT_BOLD)))
            _FONT_REGULAR = "Caveat"
            _FONT_BOLD = "Caveat-Bold"
            logger.info("Caveat font loaded from %s", _FONTS_DIR)
        else:
            logger.warning(
                "Caveat font files not found in %s — falling back to Helvetica",
                _FONTS_DIR,
            )
    except Exception as exc:
        logger.warning("Could not register Caveat font (%s) — falling back to Helvetica", exc)

_register_fonts()

# ------------------------------------------------------------------ #
# Brand palette & layout constants                                     #
# ------------------------------------------------------------------ #
_PARCHMENT = colors.white           # white bg — prints cleanly
_BROWN = colors.black               # pure black text/decorations
_DARK_GREY = colors.HexColor("#333333")
_RULE_COLOR = colors.HexColor("#AAAAAA")  # light grey ruled lines

_PAGE_W, _PAGE_H = landscape(LETTER)   # 792 x 612 pt (landscape)
_MARGIN = 0.55 * inch

# Header band
_HEADER_H = 1.3 * inch
_HEADER_BOTTOM = _PAGE_H - _HEADER_H

# Two-column split: 35% ingredients, 65% directions
_BODY_TOP = _HEADER_BOTTOM - 0.45 * inch   # below header + info row
_BODY_BOTTOM = 0.55 * inch                  # above footer
_BODY_H = _BODY_TOP - _BODY_BOTTOM

_COL_SPLIT = _MARGIN + (_PAGE_W - 2 * _MARGIN) * 0.35
_COL_GAP = 0.10 * inch
_LEFT_COL_X = _MARGIN
_LEFT_COL_W = _COL_SPLIT - _MARGIN - _COL_GAP / 2
_RIGHT_COL_X = _COL_SPLIT + _COL_GAP / 2
_RIGHT_COL_W = _PAGE_W - _MARGIN - _RIGHT_COL_X

_LINE_SPACING = 0.46 * inch   # ruled line pitch in body (sized for 16pt font)


# ------------------------------------------------------------------ #
# Decorative drawing helpers                                           #
# ------------------------------------------------------------------ #

def _draw_paw(c: canvas.Canvas, x: float, y: float, size: float = 18,
              color: colors.Color = _BROWN) -> None:
    """Draw a simple paw print: 1 large pad + 4 toe pads."""
    c.setFillColor(color)
    # Main pad (oval)
    c.ellipse(x - size * 0.5, y - size * 0.5, x + size * 0.5, y + size * 0.4, fill=1, stroke=0)
    # 4 toe pads above
    toe_r = size * 0.22
    offsets = [
        (-size * 0.45, size * 0.42),
        (-size * 0.15, size * 0.58),
        (size * 0.15, size * 0.58),
        (size * 0.45, size * 0.42),
    ]
    for dx, dy in offsets:
        c.circle(x + dx, y + dy, toe_r, fill=1, stroke=0)


def _draw_bone(c: canvas.Canvas, x: float, y: float, width: float = 40,
               color: colors.Color = _BROWN) -> None:
    """Draw a simple dog bone shape (two end-circles + rectangle body)."""
    r = 7.0
    c.setFillColor(color)
    c.circle(x, y, r, fill=1, stroke=0)
    c.circle(x + width, y, r, fill=1, stroke=0)
    c.rect(x, y - r * 0.5, width, r, fill=1, stroke=0)


def _draw_rule(c: canvas.Canvas, x1: float, y: float, x2: float,
               color: colors.Color = _RULE_COLOR, width: float = 0.5) -> None:
    c.setStrokeColor(color)
    c.setLineWidth(width)
    c.line(x1, y, x2, y)


# ------------------------------------------------------------------ #
# Page sections                                                        #
# ------------------------------------------------------------------ #

def _draw_background(c: canvas.Canvas) -> None:
    c.setFillColor(_PARCHMENT)
    c.rect(0, 0, _PAGE_W, _PAGE_H, fill=1, stroke=0)


def _draw_header(c: canvas.Canvas, title: str, nalla_stamp_bytes: bytes = b"") -> None:
    """Draw the top header band with paw prints, bones, title, stamp and rule."""
    center_y = _HEADER_BOTTOM + _HEADER_H / 2

    # Stamp image — right side of header
    stamp_w_pt = 0.0
    if nalla_stamp_bytes:
        try:
            from PIL import Image as _PILImage  # type: ignore[import-untyped]
            img = _PILImage.open(io.BytesIO(nalla_stamp_bytes)).convert("RGBA")
            stamp_h_pt = _HEADER_H * 0.80
            ratio = stamp_h_pt / (img.height * 0.75)
            stamp_w_pt = img.width * 0.75 * ratio
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            stamp_x = _PAGE_W - _MARGIN - stamp_w_pt
            stamp_y = _HEADER_BOTTOM + (_HEADER_H - stamp_h_pt) / 2
            c.drawImage(ImageReader(buf), stamp_x, stamp_y,
                        width=stamp_w_pt, height=stamp_h_pt, mask="auto")
        except Exception as exc:
            logger.warning("Stamp skipped in header: %s", exc)
            stamp_w_pt = 0.0

    # Paw prints — left side (3)
    paw_positions_left = [
        (0.45 * inch, center_y + 0.20 * inch),
        (0.90 * inch, center_y - 0.10 * inch),
        (1.35 * inch, center_y + 0.15 * inch),
    ]
    for px, py in paw_positions_left:
        _draw_paw(c, px, py, size=18, color=_BROWN)

    # Bones flanking the title (left side only — right side has stamp)
    bone_y = center_y - 0.30 * inch
    _draw_bone(c, 1.75 * inch, bone_y, width=36, color=_BROWN)
    _draw_bone(c, 2.30 * inch, bone_y + 0.22 * inch, width=36, color=_BROWN)

    # "Nalla Recipe Card" — centered in the space left of the stamp
    title_area_w = _PAGE_W - (2.0 * inch) - (stamp_w_pt + _MARGIN + 0.1 * inch)
    title_cx = 2.0 * inch + title_area_w / 2
    c.setFont(_FONT_BOLD, 38)
    c.setFillColor(_BROWN)
    c.drawCentredString(title_cx, center_y + 0.05 * inch, "Nalla Recipe Card")

    # Horizontal rule below header band
    _draw_rule(c, _MARGIN * 0.5, _HEADER_BOTTOM, _PAGE_W - _MARGIN * 0.5,
               color=_BROWN, width=1.5)


def _draw_title_row(c: canvas.Canvas, title: str, y: float) -> float:
    """Draw 'Title: <recipe name>' on a ruled baseline. Returns y for next row."""
    label_w = 0.65 * inch
    c.setFont(_FONT_BOLD, 17)
    c.setFillColor(_BROWN)
    c.drawString(_MARGIN, y, "Title:")
    c.setFont(_FONT_REGULAR, 17)
    c.drawString(_MARGIN + label_w, y, title)
    rule_y = y - 6
    _draw_rule(c, _MARGIN, rule_y, _PAGE_W - _MARGIN, color=_RULE_COLOR)
    # Return a full line-height below so next row doesn't overlap
    return y - _LINE_SPACING


def _draw_info_row(c: canvas.Canvas, cook_temp: str, cook_time: str, y: float) -> float:
    """Draw cook-time / temp / servings info row with pipes. Returns y for body."""
    parts: list[str] = []
    if cook_time:
        parts.append(f"Cook Time: {cook_time}")
    if cook_temp:
        parts.append(f"Temp: {cook_temp}")
    parts.append("Servings: varies")

    line = "   |   ".join(parts)
    c.setFont(_FONT_REGULAR, 15)
    c.setFillColor(_BROWN)
    c.drawCentredString(_PAGE_W / 2, y, line)
    rule_y = y - 6
    _draw_rule(c, _MARGIN, rule_y, _PAGE_W - _MARGIN, color=_RULE_COLOR)
    return y - _LINE_SPACING


# ------------------------------------------------------------------ #
# Two-column body                                                      #
# ------------------------------------------------------------------ #

def _measure_text_lines(
    text: str, max_width: float, font: str, size: float
) -> list[str]:
    """Word-wrap text to fit max_width using reportlab string width."""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        trial = " ".join([*current, word])
        if stringWidth(trial, font, size) <= max_width:
            current.append(word)
        else:
            if current:
                lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines or [""]


def _draw_ruled_column_lines(
    c: canvas.Canvas, col_x: float, col_w: float,
    top_y: float, bottom_y: float
) -> None:
    """Draw horizontal ruled lines across a column area."""
    y = top_y
    while y >= bottom_y:
        _draw_rule(c, col_x, y, col_x + col_w, color=_RULE_COLOR, width=0.4)
        y -= _LINE_SPACING


def _draw_column_header(
    c: canvas.Canvas, label: str, col_x: float, col_w: float, y: float
) -> float:
    """Draw section header + rule. Returns the first text-line y (aligned to grid)."""
    c.setFont(_FONT_BOLD, 16)
    c.setFillColor(_BROWN)
    c.drawString(col_x, y, label)
    rule_y = y - 5
    _draw_rule(c, col_x, rule_y, col_x + col_w, color=_BROWN, width=0.8)
    # Return exactly one LINE_SPACING below top_y so text grid aligns with ruled lines
    return y - _LINE_SPACING


def _draw_ingredients(
    c: canvas.Canvas, ingredients: list[str],
    col_x: float, col_w: float, top_y: float, bottom_y: float
) -> None:
    """Fill ingredient text on ruled lines in the left column."""
    y = top_y
    font_size = 16
    for item in ingredients:
        if y < bottom_y + 4:
            break
        lines = _measure_text_lines(f"• {item}", col_w - 6, _FONT_REGULAR, font_size)
        for line in lines:
            if y < bottom_y + 4:
                break
            c.setFont(_FONT_REGULAR, font_size)
            c.setFillColor(_BROWN)
            # Draw text baseline 4pt above the ruled line so text sits ON the line
            c.drawString(col_x + 4, y + 4, line)
            y -= _LINE_SPACING


def _draw_directions(
    c: canvas.Canvas, instructions: list[str],
    col_x: float, col_w: float, top_y: float, bottom_y: float
) -> None:
    """Fill numbered direction steps on ruled lines in the right column."""
    y = top_y
    font_size = 16
    for idx, step in enumerate(instructions, start=1):
        if y < bottom_y + 4:
            break
        prefix = f"{idx}. "
        indent_w = 22.0
        first_line_w = col_w - 6 - indent_w
        lines = _measure_text_lines(step, first_line_w, _FONT_REGULAR, font_size)

        for i, line in enumerate(lines):
            if y < bottom_y + 4:
                break
            c.setFont(_FONT_REGULAR, font_size)
            c.setFillColor(_BROWN)
            # Draw text baseline 4pt above the ruled line so text sits ON the line
            if i == 0:
                c.drawString(col_x + 4, y + 4, prefix + line)
            else:
                c.drawString(col_x + 4 + indent_w, y + 4, line)
            y -= _LINE_SPACING


def _draw_vertical_separator(
    c: canvas.Canvas, x: float, top_y: float, bottom_y: float
) -> None:
    c.setStrokeColor(_RULE_COLOR)
    c.setLineWidth(0.8)
    c.line(x, top_y, x, bottom_y)


def _draw_two_column_body(
    c: canvas.Canvas,
    ingredients: list[str],
    instructions: list[str],
    top_y: float,
    bottom_y: float,
) -> None:
    """Draw the two-column ruled body: ingredients left, directions right."""
    sep_x = _COL_SPLIT

    # Draw ruled lines behind text
    _draw_ruled_column_lines(c, _LEFT_COL_X, _LEFT_COL_W, top_y, bottom_y)
    _draw_ruled_column_lines(c, _RIGHT_COL_X, _RIGHT_COL_W, top_y, bottom_y)

    # Vertical separator
    _draw_vertical_separator(c, sep_x, top_y + _LINE_SPACING * 0.5, bottom_y)

    # Column headers
    ingr_text_top = _draw_column_header(
        c, "Ingredients:", _LEFT_COL_X, _LEFT_COL_W, top_y
    )
    dir_text_top = _draw_column_header(
        c, "Directions:", _RIGHT_COL_X, _RIGHT_COL_W, top_y
    )

    # Fill text
    _draw_ingredients(c, ingredients, _LEFT_COL_X, _LEFT_COL_W, ingr_text_top, bottom_y)
    _draw_directions(c, instructions, _RIGHT_COL_X, _RIGHT_COL_W, dir_text_top, bottom_y)


# ------------------------------------------------------------------ #
# Footer                                                               #
# ------------------------------------------------------------------ #

def _draw_footer(c: canvas.Canvas, nalla_stamp_bytes: bytes, footer_text: str) -> None:
    footer_top = _BODY_BOTTOM
    _draw_rule(c, _MARGIN * 0.5, footer_top, _PAGE_W - _MARGIN * 0.5,
               color=_BROWN, width=1.0)

    stamp_h_pt = 0.9 * inch
    if nalla_stamp_bytes:
        try:
            from PIL import Image  # type: ignore[import-untyped]
            img = Image.open(io.BytesIO(nalla_stamp_bytes))
            ratio = stamp_h_pt / (img.height * 0.75)
            stamp_w_pt = img.width * 0.75 * ratio
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            stamp_y = footer_top - stamp_h_pt - 4
            c.drawImage(
                ImageReader(buf),
                _MARGIN,
                stamp_y,
                width=stamp_w_pt,
                height=stamp_h_pt,
                mask="auto",
            )
        except Exception as exc:
            logger.warning("Nalla stamp skipped: %s", exc)

    # Site URL — right side of footer
    c.setFont(_FONT_REGULAR, 10)
    c.setFillColor(_DARK_GREY)
    url_y = footer_top - stamp_h_pt / 2 - 4
    c.drawRightString(_PAGE_W - _MARGIN, url_y, footer_text)


# ------------------------------------------------------------------ #
# Public API                                                           #
# ------------------------------------------------------------------ #

def generate_recipe_card_pdf(
    title: str,
    ingredients: list[str],
    instructions: list[str],
    nalla_stamp_bytes: bytes,
    cook_temp: str = "",
    cook_time: str = "",
    *,
    header_title: str = "Recipe Card",
    footer_text: str = "",
) -> bytes:
    """Render a Parchment & Paws recipe card PDF and return the raw bytes."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=landscape(LETTER))
    c.setTitle(title)

    _draw_background(c)
    _draw_header(c, header_title, nalla_stamp_bytes)

    # Title + info rows just below header — leave breathing room under the rule
    y = _HEADER_BOTTOM - 0.30 * inch
    y = _draw_title_row(c, title, y)
    y = _draw_info_row(c, cook_temp, cook_time, y)

    # Two-column ruled body
    body_top = y - 4
    _draw_two_column_body(c, ingredients, instructions, body_top, _BODY_BOTTOM)

    # Footer (site URL only — stamp is in header)
    _draw_footer(c, b"", footer_text)

    c.save()
    logger.info(
        "Generated Parchment & Paws PDF: font=%s, ingredients=%d, steps=%d",
        _FONT_REGULAR,
        len(ingredients),
        len(instructions),
    )
    return buf.getvalue()
