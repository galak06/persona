# Recipe Card Image Templates

Branded "recipe card" images (4:5, **1080×1350**) for Instagram / Facebook image
posts, in the **Nalla's Dad** voice for [dogfoodandfun.com](https://dogfoodandfun.com).

This is a **template exploration** step: three distinct styles to choose from.
It is self-contained — nothing in `prepare.py` / `publish_prepared.py` is wired up
yet. Integration comes after a style is picked.

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
sibling `dogfoodandfun/` dir.

## How to render

```bash
BRAND_DIR=/path/to/dogfoodandfun python recipe-publisher/templates/recipe_card/render.py
```

Renders the three example PNGs into `<BRAND_DIR>/data/recipe_card_templates/examples/`.
Each card is rendered with
Playwright/chromium at 2× device-scale for crisp type, then downscaled to the
exact 1080×1350 spec. Templates load local fonts and photos via absolute
`file://` URLs, so the script navigates to a temp HTML file (CSS `file://`
backgrounds are blocked under `page.set_content`).

If chromium is missing: `python -m playwright install chromium`.

## The three styles

| Style | File | Layout | Accent |
|---|---|---|---|
| **A** split collage | `template_a_split_collage.html` | Top: cream text panel (title + paw divider + ingredients + URL pill) beside a hero photo. Bottom: 2-up photo grid. Brown footer ribbon. | purple `#5b4b8a` |
| **B** photo top | `template_b_photo_top.html` | Full-bleed hero on top ~58%. Bottom cream panel: hand-lettered title, prep/cook/makes chips, circular cut-out + "you will need…" label with squiggly arrow, paw-bulleted ingredients (with parenthetical prep notes). | terracotta `#c2683d` |
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
  `dogfoodandfun/campaigns/recipes/ready/<seed-id>/`: `featured.jpg` (hero) and
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
"…full recipe at dogfoodandfun.com" line).

To render a different recipe/style, edit the `JOBS` list in `render.py` or call
`render(RenderJob(style, seed_id, out_name), out_dir)` directly.

## Files

**ENGINE** (`recipe-publisher/templates/recipe_card/`):

- `render.py` — Playwright renderer + per-style HTML builder (`RenderJob`, `build_html`, `render`).
- `recipe_data.py` — `RecipeCardData` dataclass + `load_recipe_card` loader.

**BRAND** (`<BRAND_DIR>/data/recipe_card_templates/`):

- `template_*.html` — the three styles (string-substitution placeholders, no Jinja).
- `examples/` — the three rendered example PNGs.
- `review/` — standalone, browser-previewable refinements of the three styles (see below).

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
