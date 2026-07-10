# Recipe Card Image Templates

Branded "recipe card" images (4:5, **1080×1350**) for Instagram / Facebook image
posts, in the **Nalla's Dad** voice for [your-brand.com](https://your-brand.com).

This is a **template exploration** step: three distinct styles to choose from.
The carousel pipeline (`prepare.py` / `publish_prepared.py`) is still not wired to
these cards. However, a **DB-backed render path now exists** and is the supported
way to produce a card today (see "Rendering from the recipe DB" below).

## Engine / brand split

The renderer and its data loader stay in the **engine** repo; the brand-specific
templates, browser-preview `review/`, and rendered `examples/` live in the
**brand** dir and are resolved at runtime via the `BRAND_DIR` env var.

| Lives in ENGINE (`recipe-publisher/templates/recipe_card/`) | Lives in BRAND (`<BRAND_DIR>/data/recipe_card_templates/`) |
|---|---|
| `render.py`, `recipe_data.py`, `README.md` | `template_*.html`, `review/`, `examples/` |

`render.py` reads `template_*.html` from `<BRAND_DIR>/data/recipe_card_templates/`
and writes rendered PNGs to its `examples/` subdir. Fonts (`Caveat`) remain shared
engine infra under `assets/fonts/`. If `BRAND_DIR` is unset, it falls back to the
sibling `persona/` dir.

## How to render

```bash
BRAND_DIR=/path/to/persona python recipe-publisher/templates/recipe_card/render.py
```

Renders the example PNGs into `<BRAND_DIR>/data/recipe_card_templates/examples/`.
Each card is rendered with
Playwright/chromium at 2× device-scale for crisp type, then downscaled to the
exact 1080×1350 spec. Templates load local fonts and photos via absolute
`file://` URLs, so the script navigates to a temp HTML file (CSS `file://`
backgrounds are blocked under `page.set_content`).

If chromium is missing: `python -m playwright install chromium`.

## The two styles

> **Template A (split collage) was removed** — it needs three distinct photos and
> repeats the single hero when a recipe has no generated slides, and its ingredient
> panel overflows the URL pill on longer lists. **B** is the default.

| Style | File | Layout | Accent |
|---|---|---|---|
| **B** photo top (default) | `template_b_photo_top.html` | Full-bleed hero on top ~58%. Bottom cream panel: hand-lettered title, prep/cook/makes chips, circular cut-out + "you will need…" label with squiggly arrow, paw-bulleted ingredients (with parenthetical prep notes). | terracotta `#c2683d` |
| **C** framed overlay (original) | `template_c_framed_overlay.html` | Full-bleed hero photo with a floating translucent cream recipe-card: eyebrow, two-tone title, paw divider, two columns (Ingredients · numbered Method), bottom URL tagline. Magazine-clean. | green `#3f7a6b` |

Shared brand palette: cream `#f5efe5`, dark brown `#3a221a`, near-black `#1a1a1a`,
rule grey. Fonts: Caveat (hand-lettered, `assets/fonts/`), Arial Black (display),
Arial/Helvetica (body). Paw motif 🐾 as divider, mirroring
`recipe-publisher/generators/text_overlay.py`.

## Data contract (for wiring into prepare.py later)

`recipe_data.RecipeCardData` is built by `load_recipe_card(seed_id)`, which reads:

- **Title / ingredients / steps / timing** from the seed entry in
  `recipe-publisher/seeds/seeds.json` (matched on `id`), with `metadata.json`
  in the campaign folder as a title fallback.
- **Photos** from the ready campaign folder
  `persona/campaigns/recipes/ready/<seed-id>/`: `featured.jpg` (hero) and
  `slides/slide_*.jpg` (collage / cut-out photos).

```python
@dataclass
class RecipeCardData:
    seed_id: str
    title: str
    ingredients: list[str]
    steps: list[str]
    prep_minutes: int | None
    cook_minutes: int | None
    yield_servings: str
    hero_path: Path | None
    slide_paths: list[Path]
```

Minimum required to render any card: **title**, **ingredients**, **hero_path**.
Style B additionally uses prep/cook/yield + one slide as the cut-out; style C
uses `steps`. Long ingredient/step lists are capped gracefully (first N, then a
"…full recipe at your-brand.com" line).

To render a different recipe/style, edit the `JOBS` list in `render.py` or call
`render(RenderJob(style, seed_id, out_name), out_dir)` directly.

## Rendering from the recipe DB (supported path)

`scripts/render_card_from_db.py` renders a card straight from a `recipes.db` row —
no seed export or `ready/` campaign folder required:

```bash
BRAND_DIR=/path/to/persona \
  python recipe-publisher/scripts/render_card_from_db.py --id <recipe-slug> [--style a|b|c]
```

It builds a `RecipeCardData` from the DB (title from `display_name`/`name`,
ingredients with decimal→fraction cleanup), reuses the recipe's local
`images/featured.jpg` artifact as the hero (downloading `hero_image_url` only if
absent), then calls `render_card(job, out_dir, card)` — the data-driven entrypoint
split out of `render()` for exactly this purpose.

Output + DB side effects (so the web frontend lists it):

- writes `<BRAND_DIR>/data/recipe_artifacts/<id>/images/recipe_card.png`
- sets `recipes.artifacts_path`, `recipes.card_path`, `recipes.card_created_at`
  (via `RecipeRepository.set_artifacts_path` / `set_card`)

The browse API then exposes `card_path` + `card_created_at` on the recipe, and the
artifacts viewer (`GET /api/v1/recipes/{id}/artifacts`) lists the rendered card.

Default style is **B** (single hero, no photo-grid repetition). For a recipe with
no generated slides, B's circular cut-out falls back to the hero image.

## Files

**ENGINE** (`recipe-publisher/templates/recipe_card/`):

- `render.py` — Playwright renderer + per-style HTML builder (`RenderJob`, `build_html`, `render`).
- `recipe_data.py` — `RecipeCardData` dataclass + `load_recipe_card` loader.

**BRAND** (`<BRAND_DIR>/data/recipe_card_templates/`):

- `template_*.html` — the two styles (string-substitution placeholders, no Jinja).
- `examples/` — the rendered example PNGs.
- `review/` — standalone, browser-previewable refinements of the styles (see below).

## `review/` — browser preview & design tweaking

`review/` holds refined, **fully self-contained** versions of the same three
styles, built for eyeballing **colors, typography, layout, and copy** directly in
a browser — no Playwright, no build step, no local `file://` assets.

- **How to open:** just double-click `review/index.html` (a one-page gallery that
  embeds all three cards as iframes with accent swatches + descriptions). Or open
  any `review/template_*.html` on its own.
- **Self-contained:** fonts load from Google Fonts CDN — **Anton** (heavy display
  titles), **Caveat** 700 (hand-lettered script), **Inter** (body/labels). Every
  photo area is a styled **placeholder** `<div>` (dashed border + monospace label
  naming the shot and target ratio), *not* a real image.
- **Theming surface:** every file exposes its design tokens in a commented
  `:root{}` block — `--accent`, `--cream`, `--brown`, `--ink`, `--rule`,
  `--title-font`, `--script-font`, `--body-font`, plus `--title-size`, `--pad`,
  `--radius`, `--placeholder-bg`. Tweak there and refresh. Accents per style:
  A purple `#5b4b8a`, B terracotta `#c2683d`, C green `#3f7a6b`.
- The card itself stays at exact **1080 × 1350** internal coordinates; a CSS
  `transform: scale()` wrapper shrinks the whole card to fit the viewport.

Once a style is chosen, these get wired into the Playwright renderer (`render.py`)
— the placeholder `<div>`s swap back to real `file://` photo backgrounds and the
baked-in sample copy becomes string-substitution placeholders fed from
`recipe_data.py`.
