"""
Parse WP REST API post HTML into structured RecipeData.
Uses stdlib html.parser only — no third-party deps.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

logger = logging.getLogger(__name__)

_INGREDIENT_RE = re.compile(r"ingredient", re.IGNORECASE)
_INSTRUCTION_RE = re.compile(
    r"instruction|direction|step|how\s+to", re.IGNORECASE
)
_TEMP_RE = re.compile(r"\b(\d{2,3})\s*°?\s*F\b")
_TIME_RE = re.compile(r"\b(\d+(?:[–\-]\d+)?)\s*(hour|hr|minute|min)s?\b", re.IGNORECASE)


@dataclass
class RecipeData:
    title: str
    ingredients: list[str] = field(default_factory=list)
    instructions: list[str] = field(default_factory=list)
    cook_temp: str = ""
    cook_time: str = ""


class _RecipeParser(HTMLParser):
    """Single-pass parser that tracks headings and list items."""

    def __init__(self) -> None:
        super().__init__()
        self._current_tag: str = ""
        self._current_text: list[str] = []
        self._in_li: bool = False
        self._in_heading: bool = False
        # "ingredients" | "instructions" | None
        self._active_section: str | None = None
        self.ingredients: list[str] = []
        self.instructions: list[str] = []
        self.full_text: list[str] = []  # all visible text for bonus fields

    # ------------------------------------------------------------------ #
    # HTMLParser callbacks                                                 #
    # ------------------------------------------------------------------ #

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._current_tag = tag
        if tag in ("h1", "h2", "h3", "h4"):
            self._in_heading = True
            self._current_text = []
        elif tag == "li":
            self._in_li = True
            self._current_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("h1", "h2", "h3", "h4") and self._in_heading:
            heading = "".join(self._current_text).strip()
            self._in_heading = False
            self._current_text = []
            if _INGREDIENT_RE.search(heading):
                self._active_section = "ingredients"
            elif _INSTRUCTION_RE.search(heading):
                self._active_section = "instructions"
            else:
                # Any other heading resets the active section
                self._active_section = None
        elif tag == "li" and self._in_li:
            text = "".join(self._current_text).strip()
            self._in_li = False
            self._current_text = []
            if not text:
                return
            if self._active_section == "ingredients":
                self.ingredients.append(text)
            elif self._active_section == "instructions":
                self.instructions.append(text)

    def handle_data(self, data: str) -> None:
        cleaned = data.strip()
        if cleaned:
            self.full_text.append(cleaned)
        if self._in_heading or self._in_li:
            self._current_text.append(data)


# ------------------------------------------------------------------ #
# Public API                                                           #
# ------------------------------------------------------------------ #

def _extract_cook_temp(text: str) -> str:
    m = _TEMP_RE.search(text)
    if m:
        return f"{m.group(1)}°F"
    return ""


def _extract_cook_time(text: str) -> str:
    m = _TIME_RE.search(text)
    if m:
        value = m.group(1)
        unit = m.group(2).lower()
        # Normalise abbreviations
        if unit.startswith("hr") or unit.startswith("hour"):
            unit_label = "hour" if value == "1" else "hours"
        else:
            unit_label = "minute" if value == "1" else "minutes"
        return f"{value} {unit_label}"
    return ""


def parse_recipe(title: str, html_content: str) -> RecipeData:
    """Parse WP content.rendered HTML into a RecipeData struct.

    Falls back to empty ingredient/instruction lists when no structured
    headings are found — caller must handle the empty-list case.
    """
    parser = _RecipeParser()
    try:
        parser.feed(html_content)
    except Exception as exc:
        logger.warning("HTML parse error, returning partial data: %s", exc)

    full_text = " ".join(parser.full_text)

    data = RecipeData(
        title=title,
        ingredients=parser.ingredients,
        instructions=parser.instructions,
        cook_temp=_extract_cook_temp(full_text),
        cook_time=_extract_cook_time(full_text),
    )

    if not data.ingredients and not data.instructions:
        logger.warning(
            "No structured recipe sections found in post '%s'. "
            "Returning empty ingredients/instructions.",
            title,
        )

    return data
