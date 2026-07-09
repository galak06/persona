"""Normalize a raw schema.org Recipe dict into a ``ScrapedRecipe``.

Pure transformation, no network. Defensive against missing/partial keys:
never raises on a partially-formed recipe — blanks are filled instead.
"""
from __future__ import annotations

import hashlib
import html
import re
from urllib.parse import urlparse

from recipe_db.models import Ingredient, ScrapedRecipe

# Two-label public suffixes where the registered domain needs three labels
# (e.g. "bbc.co.uk"). Small curated set; good enough without tldextract.
_TWO_LABEL_SUFFIXES = {
    "co.uk",
    "org.uk",
    "ac.uk",
    "gov.uk",
    "co.nz",
    "co.za",
    "com.au",
    "com.br",
    "co.in",
    "co.jp",
}

_ISO_DURATION_RE = re.compile(
    r"^P"
    r"(?:(?P<days>\d+)D)?"
    r"(?:T"
    r"(?:(?P<hours>\d+)H)?"
    r"(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+)S)?"
    r")?$",
    re.IGNORECASE,
)


def parse_iso8601_duration(value: object) -> int:
    """Convert an ISO-8601 duration (``PT1H30M``) to whole minutes.

    Missing/None/non-string or unparseable input yields 0.
    """
    if not isinstance(value, str):
        return 0
    match = _ISO_DURATION_RE.match(value.strip())
    if match is None:
        return 0
    parts = match.groupdict()
    days = int(parts["days"] or 0)
    hours = int(parts["hours"] or 0)
    minutes = int(parts["minutes"] or 0)
    seconds = int(parts["seconds"] or 0)
    return days * 24 * 60 + hours * 60 + minutes + seconds // 60


def _registered_domain(source_url: str) -> str:
    """Best-effort registered domain (e.g. ``allrecipes.com``)."""
    host = (urlparse(source_url).hostname or "").lower()
    if not host:
        return ""
    if host.startswith("www."):
        host = host[4:]
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    last_two = ".".join(labels[-2:])
    if last_two in _TWO_LABEL_SUFFIXES and len(labels) >= 3:
        return ".".join(labels[-3:])
    return last_two


def _as_text(value: object) -> str:
    """Coerce a scalar-ish value to a stripped, HTML-unescaped string.

    JSON-LD payloads often embed HTML entities (``&#39;`` -> ``'``); decoding
    here keeps recipe names, slugs, and seed titles clean.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return html.unescape(value).strip()
    if isinstance(value, (int, float)):
        return str(value)
    return html.unescape(str(value)).strip()


def _first_text(value: object) -> str:
    """First usable string from a str / list / dict-with-url-or-name."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        for item in value:
            text = _first_text(item)
            if text:
                return text
        return ""
    if isinstance(value, dict):
        for key in ("url", "value", "name", "text"):
            if value.get(key):
                return _first_text(value[key])
    return ""


def _parse_ingredient(line: str) -> Ingredient:
    """Whole line into ``item``; qty/unit/notes left blank (robust default)."""
    return Ingredient(item=line.strip(), qty="", unit="", notes="")


def _normalize_ingredients(value: object) -> list[Ingredient]:
    if isinstance(value, str):
        lines = [ln for ln in value.splitlines() if ln.strip()]
    elif isinstance(value, list):
        lines = [_as_text(v) for v in value]
    else:
        lines = []
    return [_parse_ingredient(ln) for ln in lines if ln.strip()]


def _normalize_instructions(value: object) -> list[str]:
    """Handle list[str], list[HowToStep/HowToSection], or newline string."""
    steps: list[str] = []
    if isinstance(value, str):
        steps = [ln.strip() for ln in value.splitlines() if ln.strip()]
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                if item.strip():
                    steps.append(item.strip())
            elif isinstance(item, dict):
                item_type = item.get("@type", "")
                if isinstance(item_type, list):
                    item_type = item_type[0] if item_type else ""
                if str(item_type).endswith("HowToSection"):
                    section = item.get("itemListElement", [])
                    steps.extend(_normalize_instructions(section))
                else:
                    text = _as_text(item.get("text") or item.get("name"))
                    if text:
                        steps.append(text)
    elif isinstance(value, dict):
        steps = _normalize_instructions([value])
    return steps


def _normalize_servings(value: object) -> str:
    if isinstance(value, list):
        return _first_text(value)
    return _as_text(value)


def _normalize_nutrition(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for key, raw in value.items():
        if key == "@type":
            continue
        text = _as_text(raw)
        if text:
            result[str(key)] = text
    return result


def _normalize_tags(value: object) -> list[str]:
    if isinstance(value, str):
        return [t.strip() for t in value.split(",") if t.strip()]
    if isinstance(value, list):
        tags: list[str] = []
        for item in value:
            text = _as_text(item)
            if text:
                tags.append(text)
        return tags
    return []


def _content_hash(name: str, ingredients: list[Ingredient]) -> str:
    lines = sorted(ing.item.strip().lower() for ing in ingredients)
    payload = name.strip().lower() + "\n" + "\n".join(lines)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize(raw: dict[str, object], source_url: str) -> ScrapedRecipe:
    """Map a schema.org Recipe dict onto the ``ScrapedRecipe`` contract."""
    raw = raw or {}

    name = _as_text(raw.get("name"))
    ingredients = _normalize_ingredients(raw.get("recipeIngredient"))
    steps = _normalize_instructions(raw.get("recipeInstructions"))

    prep_minutes = parse_iso8601_duration(raw.get("prepTime", ""))
    cook_minutes = parse_iso8601_duration(raw.get("cookTime", ""))
    total_minutes = parse_iso8601_duration(raw.get("totalTime", ""))
    if total_minutes == 0:
        total_minutes = prep_minutes + cook_minutes

    return ScrapedRecipe(
        name=name,
        ingredients=ingredients,
        steps=steps,
        prep_minutes=prep_minutes,
        cook_minutes=cook_minutes,
        total_minutes=total_minutes,
        servings=_normalize_servings(raw.get("recipeYield")),
        nutrition=_normalize_nutrition(raw.get("nutrition")),
        category=_first_text(raw.get("recipeCategory")),
        tags=_normalize_tags(raw.get("keywords")),
        hero_image_url=_first_text(raw.get("image")),
        source_url=source_url,
        source_name=_registered_domain(source_url),
        license=_as_text(raw.get("license")),
        content_hash=_content_hash(name, ingredients),
    )
