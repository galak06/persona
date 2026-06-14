"""Render branded recipe-card images (4:5, 1080x1350) via Playwright.

Three template styles are supported:

* ``a`` — split collage (cream text panel + hero, 2-up photo grid below)
* ``b`` — full-bleed hero on top, hand-lettered cream recipe panel below
* ``c`` — framed magazine card: hero photo with a floating recipe-card overlay

Run::

    python recipe-publisher/templates/recipe_card/render.py

which renders the three example PNGs into ``examples/``. See README.md for the
data contract used to wire this into prepare.py later.
"""

# pyright: reportMissingImports=false, reportMissingModuleSource=false
# (mirrors social-automation/pyrightconfig.json; the PostToolUse hook type-checks
#  a /tmp copy where the project venv + config don't apply, so resolve inline.)
from __future__ import annotations

import html
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from playwright.sync_api import FloatRect, ViewportSize, sync_playwright

from recipe_data import RecipeCardData, load_recipe_card

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]

# Fonts stay in the ENGINE assets — shared infra, brand-agnostic.
FONTS = REPO_ROOT / "assets" / "fonts"
CAVEAT_REGULAR = (FONTS / "Caveat-Regular.ttf").as_uri()
CAVEAT_BOLD = (FONTS / "Caveat-Bold.ttf").as_uri()


def _brand_dir() -> Path:
    """Resolve the brand dir from ``BRAND_DIR`` (recipe-publisher convention).

    Mirrors ``recipe-publisher/prepare.py``: read the ``BRAND_DIR`` env, falling
    back to the sibling ``dogfoodandfun`` dir when unset so local runs still work.
    """
    brand_dir = os.environ.get("BRAND_DIR")
    if brand_dir:
        return Path(brand_dir)
    return REPO_ROOT.parent / "dogfoodandfun"


# Brand-owned templates + rendered examples live under the brand dir.
TEMPLATES_DIR = _brand_dir() / "data" / "templates" / "recipe_card_templates"

WIDTH = 1080
HEIGHT = 1350
VIEWPORT: ViewportSize = {"width": WIDTH, "height": HEIGHT}
CLIP: FloatRect = {"x": 0, "y": 0, "width": WIDTH, "height": HEIGHT}
SCALE = 2
MAX_INGREDIENTS = 7
MAX_STEPS = 6

# One tasteful warm accent per template.
# TODO: move accents/byline/domain to brand.json visual block when design is locked
ACCENTS = {"b": "#c2683d", "c": "#3f7a6b"}


@dataclass
class RenderJob:
    style: str
    seed_id: str
    out_name: str


def _two_tone_title(title: str) -> str:
    """First word(s) accent, remainder dark — split near the midpoint."""
    words = title.split()
    if len(words) <= 1:
        return f'<span class="accent">{html.escape(title)}</span>'
    mid = max(1, len(words) // 2)
    head = html.escape(" ".join(words[:mid]))
    tail = html.escape(" ".join(words[mid:]))
    return f'<span class="accent">{head}</span> <span class="dark">{tail}</span>'


def _hand_title(title: str) -> str:
    words = title.split()
    if len(words) <= 2:
        return html.escape(title)
    mid = len(words) // 2
    head = html.escape(" ".join(words[:mid]))
    tail = html.escape(" ".join(words[mid:]))
    return f'{head}<br><span class="accent">{tail}</span>'


def _split_note(line: str) -> str:
    """Wrap a parenthetical prep note in a <span class="note">."""
    esc = html.escape(line)
    if "(" in esc and ")" in esc:
        start = esc.index("(")
        end = esc.rindex(")") + 1
        return f"{esc[:start]}<span class=\"note\">{esc[start:end]}</span>{esc[end:]}"
    return esc


def _ingredients_html(items: list[str], *, with_notes: bool) -> str:
    shown = items[:MAX_INGREDIENTS]
    lis = []
    for it in shown:
        body = _split_note(it) if with_notes else html.escape(it)
        lis.append(f"<li>{body}</li>")
    if len(items) > MAX_INGREDIENTS:
        lis.append('<li class="more">…full recipe at dogfoodandfun.com</li>')
    return "".join(lis)


def _steps_html(items: list[str]) -> str:
    shown = items[:MAX_STEPS]
    lis = [f"<li>{html.escape(s)}</li>" for s in shown]
    if len(items) > MAX_STEPS:
        lis.append('<li class="more">…full method at dogfoodandfun.com</li>')
    return "".join(lis)


def _fmt_minutes(value: int | None) -> str:
    return f"{value} min" if value else "—"


def _short_yield(text: str) -> str:
    """Compact a long yield string into a chip-sized phrase."""
    text = text.strip()
    if "(" in text:
        text = text[: text.index("(")].strip()
    text = text.replace("makes ", "").replace("feeds ", "")
    return text[:22] if text else "1 batch"


def build_html(job: RenderJob, data: RecipeCardData) -> str:
    template = TEMPLATES_DIR / f"template_{ {'b':'b_photo_top','c':'c_framed_overlay'}[job.style] }.html"
    markup = template.read_text(encoding="utf-8")

    hero_uri = data.hero_path.as_uri()
    repl: dict[str, str] = {
        "{{accent}}": ACCENTS[job.style],
        "{{caveat_regular}}": CAVEAT_REGULAR,
        "{{caveat_bold}}": CAVEAT_BOLD,
        "{{hero_url}}": hero_uri,
    }

    if job.style == "b":
        cutout = (data.slide_paths[2] if len(data.slide_paths) > 2 else data.hero_path).as_uri()
        repl.update(
            {
                "{{title_html}}": _hand_title(data.title),
                "{{ingredients_html}}": _ingredients_html(data.ingredients, with_notes=True),
                "{{cutout_url}}": cutout,
                "{{prep}}": _fmt_minutes(data.prep_minutes),
                "{{cook}}": _fmt_minutes(data.cook_minutes),
                "{{yield_short}}": html.escape(_short_yield(data.yield_servings)),
            }
        )
    else:  # c
        repl.update(
            {
                "{{title_html}}": _two_tone_title(data.title),
                "{{ingredients_html}}": _ingredients_html(data.ingredients, with_notes=False),
                "{{steps_html}}": _steps_html(data.steps),
            }
        )

    for key, value in repl.items():
        markup = markup.replace(key, value)
    return markup


def verify_assets(data: RecipeCardData) -> list[str]:
    """Return a list of missing asset paths (empty == all resolve)."""
    missing: list[str] = []
    paths = [data.hero_path, *data.slide_paths, CAVEAT_REGULAR, CAVEAT_BOLD]
    for p in paths:
        local = Path(p.replace("file://", "")) if isinstance(p, str) else p
        if local and not Path(local).exists():
            missing.append(str(local))
    return missing


def render(job: RenderJob, out_dir: Path) -> Path:
    """Render ``job`` from its seed/campaign folder (the seeds.json path)."""
    return render_card(job, out_dir, load_recipe_card(job.seed_id))


def render_card(job: RenderJob, out_dir: Path, card: RecipeCardData) -> Path:
    """Render ``job`` from already-resolved card data (e.g. a DB-backed source)."""
    missing = verify_assets(card)
    if missing:
        for m in missing:
            logger.warning("missing asset: %s", m)
    markup = build_html(job, card)

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / job.out_name

    # Write the populated HTML to a real file and navigate to it. file:// URLs in
    # CSS background-image are blocked under page.set_content() (no document
    # origin), so a goto(file://...) is required for the photos to load.
    tmp_html = out_dir / f".{job.out_name}.html"
    tmp_html.write_text(markup, encoding="utf-8")

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--allow-file-access-from-files"])
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=SCALE)
        page.goto(tmp_html.as_uri(), wait_until="networkidle")
        page.wait_for_timeout(300)
        page.screenshot(path=str(out_path), clip=CLIP)
        browser.close()

    tmp_html.unlink(missing_ok=True)

    # Render at 2x for crispness, then downscale to the exact 1080x1350 spec.
    with Image.open(out_path) as img:
        if img.size != (WIDTH, HEIGHT):
            img.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS).save(out_path)

    logger.info("rendered %s (%s)", out_path, card.seed_id)
    return out_path


JOBS = [
    RenderJob("b", "hearty-spring-beef-veggie-bowl", "example_b_photo_top.png"),
    RenderJob("c", "raw-beef-organ-patties-barf", "example_c_framed_overlay.png"),
]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    out_dir = TEMPLATES_DIR / "examples"
    for job in JOBS:
        logger.info("rendering style %s (%s) ...", job.style, job.out_name)
        render(job, out_dir)
    logger.info("done")


if __name__ == "__main__":
    main()
