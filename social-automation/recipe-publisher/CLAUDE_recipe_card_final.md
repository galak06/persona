# Recipe Card HTML Worker — Implementation Reference

## What was built

A new `worker_html.py` that renders a self-contained 1080×1080 HTML recipe card
for every recipe that has a WP post or a locally-generated hero image.
A separate render worker (future) will screenshot the HTML → PNG.

---

## Files created / modified

| File | Change |
|---|---|
| `workers/worker_html.py` | **NEW** — DB-polling worker |
| `templates/recipe_card/card.html` | **NEW** — Jinja2 card template |
| `recipe_db/models.py` | Added `card_html_path`, `card_html_created_at` fields |
| `recipe_db/db.py` | Added two `_ADDED_COLUMNS` migrations |
| `recipe_db/repository.py` | Added `set_card_html()` method |
| `workers/_folder.py` | Fixed `badge_path()` → `$BRAND_DIR/images/badge.png` |
| `api/recipe_schemas.py` | Added `card_html_path`, `card_html_created_at` to `RecipeSummary` |
| `api/recipes_api.py` | Wired new fields in `_to_summary()` via `_abs_artifacts()` |
| `frontend/src/api/recipes.ts` | Added fields to `RecipeCardFields` |
| `frontend/src/pages/RecipeDrawer.tsx` | "View HTML" button opens `file://{card_html_path}` |
| `requirements.txt` | Added `jinja2>=3.1`, `qrcode[pil]` |

---

## Worker: `worker_html.py`

### Poll predicate
```
(image_created_at truthy  OR  wp_url truthy)  AND  card_html_created_at == ""
```

### Output
Writes `post_image_card.html` to the campaign folder, stamps
`card_html_path` + `card_html_created_at` in the DB.

### Key functions

**`_wp_credentials()`**
Reads `$PROJECT_ROOT/.claude/settings.local.json` (parents[3] from `worker_html.py`).
Returns `(wp_base_url, basic_auth_token)`.

**`_wp_get(url, token)`**
One-liner authenticated GET → parsed JSON.

**`_fetch_wp_post(row, dest)`**
- Fetches WP post by slug from `row.wp_url`
- Downloads featured image to `dest` if not already present
- Returns the WP post title (`str | None`)
- All `urllib.request` calls carry `# noqa: S310`

**`_make_qr_b64(url)`**
Generates a styled QR code: terracotta fill (`#B5651D`), cream background (`#FAF7F0`),
rounded modules. Returns base64-encoded PNG.
Imports `qrcode`, `qrcode.constants`, `StyledPilImage`, `RoundedModuleDrawer` inline.

**`_do_one(repo, row)`**
1. Calls `_fetch_wp_post(row, hero_path)` — downloads image if missing
2. Base64-encodes hero image and badge PNG
3. Reads ingredients from `generated_content` or falls back to seeds
4. Builds meta chips (time, servings, difficulty, Nalla's Fave)
5. Generates QR from `row.wp_url`
6. Renders Jinja2 template with `autoescape=select_autoescape(["html"])`
7. Writes HTML, calls `repo.set_card_html()`

### Recipe title priority
```python
recipe_name = row.display_name or row.name
```
`display_name` is kept in sync with WP post titles (see DB sync below).

### Run commands
```bash
python -m workers.worker_html                     # dry-run plan
python -m workers.worker_html --apply --limit 1   # build one
python -m workers.worker_html --apply             # build all eligible
python -m workers.worker_html --health-check      # check deps → 0/1
```

### Force rebuild all
```python
conn.execute("UPDATE recipes SET card_html_path = '', card_html_created_at = ''")
conn.commit()
```
Then re-run `--apply`.

---

## Template: `templates/recipe_card/card.html`

### Layout (1080×1080)
```
┌─────────────────────────────────┐
│  PHOTO PANEL (460px)            │
│  [brand badge TL] [nalla TR]    │
│  [food photo]                   │
│  [spread tag]  [wave bottom]    │
├──────────────────────┬──────────┤
│  CONTENT PANEL       │ QR COL  │
│  Recipe title        │ (190px) │
│  Meta chips          │         │
│  "you will need" ↓   │ 📱 Scan │
│  • Ingredient 1      │ for the │
│  • Ingredient 2      │ full    │
│  • …                 │ recipe  │
│                      │ [QR]    │
│ ─────────────────────┴─────────│
│  dogfoodandfun.com  🐾 Nalla   │
└─────────────────────────────────┘
```

### Template variables
| Variable | Type | Description |
|---|---|---|
| `recipe_name` | `str` | WP post title via `display_name` |
| `ingredients` | `list[str]` | Up to 7 items |
| `meta_chips` | `list[{label, dark}]` | Time / servings / Easy / Nalla's Fave |
| `food_photo_b64` | `str\|None` | Hero image, base64 JPEG |
| `badge_b64` | `str\|None` | Nalla badge, base64 PNG (pre-fixed alpha) |
| `qr_b64` | `str\|None` | QR code pointing to `wp_url`, base64 PNG |

### Key CSS values
- Photo panel: `height: 460px`
- QR column: `width: 190px; background: #B5651D; border-radius: 20px`
- QR image: `168px × 168px`
- QR scan message: Fredoka One, `28px`
- Ingredient font: `26px`, `font-weight: 700`
- Title: Fredoka One, `38px`
- Brand color: `#B5651D` (terracotta)
- Background cream: `#FAF7F0`

---

## DB columns added

```sql
ALTER TABLE recipes ADD COLUMN card_html_path TEXT DEFAULT '';
ALTER TABLE recipes ADD COLUMN card_html_created_at TEXT DEFAULT '';
```

Added to `_ADDED_COLUMNS["recipes"]` in `recipe_db/db.py` (idempotent).

---

## `display_name` sync from WordPress

All 25 published recipes have `display_name` set to the actual WP post title
(HTML entities decoded via `html.unescape()`).

To re-sync after WP title changes:
```python
from workers.worker_html import _wp_credentials, _wp_get
from html import unescape
# For each row with wp_url:
slug = row.wp_url.rstrip("/").rsplit("/", 1)[-1]
posts = _wp_get(f"{wp_base}/wp-json/wp/v2/posts?slug={slug}&_fields=title", token)
wp_title = unescape((posts[0].get("title") or {}).get("rendered", "").strip())
repo.set_display_name(row.id, wp_title)
```

---

## Badge PNG

Fixed in-place at `$BRAND_DIR/images/badge.png` using scipy flood-fill to
remove the white background. Now has proper alpha (values: 0 and 255 only).
No runtime processing needed.

---

## Frontend wiring

**RecipeDrawer "View HTML" button** (near Generate Image):
```tsx
{recipe.card_html_path && (
  <button onClick={() => window.open(`file://${recipe.card_html_path}`, "_blank")}>
    🖼 View HTML
  </button>
)}
```
Hidden when no card HTML exists. Opens the self-contained HTML file locally.

---

## Dependencies

```
jinja2>=3.1
qrcode[pil]
```

Install: `pip install -r requirements.txt --break-system-packages`

---

## What NOT to touch

- `workers/worker_image.py` — unchanged
- `workers/_base.py` — unchanged
- `templates/post_image.html` — unchanged
- Any other worker file
