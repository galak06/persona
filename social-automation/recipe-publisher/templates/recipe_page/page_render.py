# pyright: reportMissingImports=false, reportMissingModuleSource=false
# (mirrors social-automation/pyrightconfig.json; the PostToolUse hook type-checks
#  a /tmp copy where the project venv + config don't apply, so resolve inline.)
"""Render a full recipe PAGE (HTML + CSS) from DB fields and image artifacts.

This is the web/preview counterpart to ``templates/recipe_card`` (which renders
a 4:5 social image). Here we produce the styled recipe-body HTML — the same
``.dff-recipe`` markup the WordPress publisher emits — as a real, openable file
so the post body can be verified before anything goes live.

The renderer is DB-agnostic: callers map their row into ``RecipePageData`` and
pass image references (relative paths or URLs) that resolve from wherever the
HTML is written. See ``scripts/render_page_from_db.py`` for the DB wiring.
"""

from __future__ import annotations

import html
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]


def _brand_dir() -> Path:
    """Resolve the brand dir from ``BRAND_DIR`` (recipe-publisher convention)."""
    brand_dir = os.environ.get("BRAND_DIR")
    if brand_dir:
        return Path(brand_dir)
    return REPO_ROOT.parent / "dogfoodandfun"


def _template_path() -> Path:
    return _brand_dir() / "data" / "templates" / "recipe_page_templates" / "recipe_page.html"


@dataclass
class RecipePageData:
    """Everything the page template needs, already cleaned for display.

    Image fields are reference STRINGS (e.g. ``images/featured.jpg`` relative to
    the output file, or a remote URL) — not Paths — so the renderer stays
    agnostic about where the HTML is written.
    """

    title: str
    ingredients: list[str]
    steps: list[str]
    prep_minutes: int | None = None
    cook_minutes: int | None = None
    total_minutes: int | None = None
    servings: str = ""
    category: str = ""
    tags: list[str] = field(default_factory=list)
    meta_description: str = ""
    hero_ref: str = ""
    gallery_refs: list[str] = field(default_factory=list)
    affiliate_products: list[dict[str, str]] = field(default_factory=list)
    associates_tag: str = ""
    source_name: str = ""
    source_url: str = ""


def _fmt_minutes(value: int | None) -> str:
    if not value:
        return ""
    if value >= 60:
        hours, mins = divmod(value, 60)
        return f"{hours}h {mins}m" if mins else f"{hours}h"
    return f"{value} min"


def _chip(icon: str, label: str, value: str) -> str:
    return (
        f'<span class="dff-chip">{icon} {label} '
        f'<span class="v">{html.escape(value)}</span></span>'
    )


def _meta_chips(data: RecipePageData) -> str:
    chips: list[str] = []
    if (prep := _fmt_minutes(data.prep_minutes)):
        chips.append(_chip("⏱", "Prep", prep))
    if (cook := _fmt_minutes(data.cook_minutes)):
        chips.append(_chip("🔥", "Cook", cook))
    if (total := _fmt_minutes(data.total_minutes)):
        chips.append(_chip("⏳", "Total", total))
    if data.servings.strip():
        chips.append(_chip("🍽", "Makes", data.servings.strip()))
    return "".join(chips)


def _tags_block(data: RecipePageData) -> str:
    pills: list[str] = []
    if data.category.strip():
        pills.append(f'<span class="dff-tag">{html.escape(data.category.strip())}</span>')
    pills += [f'<span class="dff-tag">{html.escape(t)}</span>' for t in data.tags if t.strip()]
    return f'<div class="dff-tags">{"".join(pills)}</div>' if pills else ""


def _hero_block(data: RecipePageData) -> str:
    if not data.hero_ref:
        return ""
    alt = html.escape(data.title, quote=True)
    return (
        f'<div class="dff-hero"><figure>'
        f'<img src="{html.escape(data.hero_ref, quote=True)}" alt="{alt}" />'
        f"</figure></div>"
    )


def _ingredients_html(items: list[str]) -> str:
    return "".join(f"<li>{html.escape(i)}</li>" for i in items if i.strip())


def _steps_html(items: list[str]) -> str:
    return "".join(f"<li>{html.escape(s)}</li>" for s in items if s.strip())


def _gallery_block(data: RecipePageData) -> str:
    imgs = [r for r in data.gallery_refs if r]
    if not imgs:
        return ""
    cells = "".join(
        f'<img src="{html.escape(r, quote=True)}" '
        f'alt="{html.escape(data.title, quote=True)}" loading="lazy" />'
        for r in imgs
    )
    return (
        '<section class="dff-section"><h2>Gallery</h2>'
        f'<div class="dff-gallery">{cells}</div></section>'
    )


def _amazon_url(asin: str, tag: str) -> str:
    base = f"https://www.amazon.com/dp/{asin}"
    return f"{base}?tag={tag}" if tag else base


def _affiliate_block(data: RecipePageData) -> str:
    products = [p for p in data.affiliate_products if p.get("asin") and p.get("display")]
    if not products:
        return ""
    cards: list[str] = []
    for p in products:
        url = _amazon_url(p["asin"], data.associates_tag)
        cards.append(
            '<div class="dff-prod">'
            f'<div class="name">{html.escape(p["display"])}</div>'
            f'<a class="buy" href="{html.escape(url, quote=True)}" '
            'target="_blank" rel="nofollow sponsored noopener">View on Amazon</a></div>'
        )
    disclosure = (
        '<p class="dff-disclosure">As an Amazon Associate we earn from '
        "qualifying purchases.</p>"
    )
    return (
        '<section class="dff-section"><h2>Tools We Use</h2>'
        f'<div class="dff-affiliate">{"".join(cards)}</div>{disclosure}</section>'
    )


def _source_top(data: RecipePageData) -> str:
    if not data.source_name.strip():
        return ""
    return f'<p class="dff-source-top">Adapted for dogs from {html.escape(data.source_name.strip())}</p>'


def _source_block(data: RecipePageData) -> str:
    if not data.source_url.strip():
        return ""
    name = html.escape(data.source_name.strip() or data.source_url.strip())
    href = html.escape(data.source_url.strip(), quote=True)
    return (
        f'<footer class="dff-source">Original recipe: '
        f'<a href="{href}" target="_blank" rel="noopener">{name}</a></footer>'
    )


def _recipe_jsonld(data: RecipePageData) -> str:
    schema: dict = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": data.title,
        "recipeCategory": data.category or "Dog treat",
        "recipeIngredient": [i for i in data.ingredients if i.strip()],
        "recipeInstructions": [
            {"@type": "HowToStep", "position": i + 1, "text": s}
            for i, s in enumerate(s for s in data.steps if s.strip())
        ],
    }
    if data.meta_description.strip():
        schema["description"] = data.meta_description.strip()
    if data.servings.strip():
        schema["recipeYield"] = data.servings.strip()
    if (total := _iso_duration(data.total_minutes)):
        schema["totalTime"] = total
    payload = json.dumps(schema, ensure_ascii=False)
    return f'<script type="application/ld+json">{payload}</script>'


def _iso_duration(minutes: int | None) -> str:
    return f"PT{minutes}M" if minutes else ""


def build_page_html(data: RecipePageData) -> str:
    """Fill the brand page template from ``data`` and return the full HTML string."""
    markup = _template_path().read_text(encoding="utf-8")
    repl = {
        "{{title}}": html.escape(data.title),
        "{{hero_block}}": _hero_block(data),
        "{{source_top}}": _source_top(data),
        "{{meta_chips}}": _meta_chips(data),
        "{{tags_block}}": _tags_block(data),
        "{{ingredients_html}}": _ingredients_html(data.ingredients),
        "{{steps_html}}": _steps_html(data.steps),
        "{{gallery_block}}": _gallery_block(data),
        "{{affiliate_block}}": _affiliate_block(data),
        "{{source_block}}": _source_block(data),
        "{{schema_jsonld}}": _recipe_jsonld(data),
    }
    for key, value in repl.items():
        markup = markup.replace(key, value)
    return markup
