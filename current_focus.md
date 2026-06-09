# Current Focus — Recipe rendering fixes, DB cleanup, publishing

_Last updated: 2026-06-09 (session)_

## System status (live site)

All recipe posts on dogfoodandfun.com are rendering correctly. Recent session
work fixed a layout bug, cleaned the recipe DB, and published one new recipe.

### Recipe posts
- 30 posts in the **recipes** category — all have working PDF cards.
- Recipe-DB viewer chips reflect reality after a `publish_status` sync pass.

## What this session did

### 1. Fixed collapsed-layout bug on 4 live posts
**Symptom:** content crushed into a ~250px column, comments flung to the right.
**Cause:** WordPress `wpautop` emits a stray `</div>` from **naked inline JSON-LD**
in post content that is NOT wrapped in `<div class="dff-recipe">`. That kicks the
post-nav + comments out of `#primary`. (Elementor `_elementor_edit_mode=builder`
was a RED HERRING — cleared it, but it wasn't the cause.)
**Fix:** re-wrapped each post's body in `dff-recipe` + moved JSON-LD to the end
(wording preserved). Verified via headless render (`#primary` back to 1160px).
- Fixed: `blueberry-yogurt-frozen-bites` (3640), `dehydrated-turkey-carrot-jerky-chews`
  (3702), `gut-supportive-bone-broth-turmeric-gelatin-squares` (3708),
  `chicken-bone-broth-for-dogs` (3647).
- Scanned all posts; these 4 were the only genuinely broken ones.
- **New posts are already safe** — the current publisher wraps in `dff-recipe`.
- Memory: `feedback_elementor_builder_hijack.md` (renamed concept:
  collapsed-layout / wpautop). See also `feedback_astra_figure_framing`.

### 2. Line-break forward fix (KEPT, uncommitted)
LLM prose was hard-wrapped → `wpautop` turned newlines into `<br>` → ragged
paragraphs. Added `generators/text_normalize.py` `unwrap_paragraphs()`, applied
in `generators/recipe_from_seed.py:assemble_body_markdown`. Tests:
`tests/test_text_normalize.py` (5, green). Also `scripts/repair_post_linebreaks.py`
(in-place WP cleanup, --dry-run default).

### 3. Recipe-DB cleanup (`${BRAND_DIR}/data/recipes.db`, backed up)
The safety scanner (`recipe_db/safety.py`) over-flags **negated** toxin mentions
("xylitol-free", "no garlic/onion") — 5 of 6 flags were FALSE POSITIVES.
- Deleted 1 genuinely-toxic row: `flea-terminator-dog-treats` (real garlic powder).
- Cleared 5 false-positive flags → `dog_safe=1` (verified by ingredient text).
- 0 rows wrongly flagged now. Backup: `recipes.db.bak-20260609-172016`.
- Scanner left AS-IS per user (false-positive bias is the safe default for a
  toxin gate). Memory: `feedback_safety_gate_false_positives.md`.

### 4. Published / synced 2 recipes
- **Dehydrated Turkey** — already live (3702); only synced DB `publish_status`
  (no duplicate).
- **Chicken/Rice/Veggie Stew** — NEW, published live:
  https://dogfoodandfun.com/lucky-and-rippys-favorite-dog-food/ (post 3972) +
  PDF card attached; DB synced. IG/FB intentionally skipped. Layout verified.
  Pipeline used: `recipe_db.cli export` → `prepare.py` → promote → `generate_recipe_card.py`.

## Open items / decisions for next session

1. **Title rename (post 3972):** currently "Lucky and Rippy's Favorite Dog Food"
   (source name, weak SEO). Consider on-brand title + slug, e.g.
   "Chicken & Rice Stew for Dogs (4 Ingredients)".
2. **Nothing committed.** Kept code changes to commit when ready:
   `generators/text_normalize.py`, `generators/recipe_from_seed.py` (unwrap),
   `tests/test_text_normalize.py`, `scripts/repair_post_linebreaks.py`.
   (Other modified files — `image.py`, `recipe.py`, `recipe_system.md`,
   `publishers/wordpress.py:set_featured_image`, `scripts/regen_hero_images.py`,
   `scripts/publish_wp_pdf_batch.py` — were pre-existing, not from this session.)
3. **`lucky-and-rippy-s-favorite-dog-food`** could be pushed to IG/FB (was scoped
   to WP+PDF only this round).
4. **Cleanup:** `prepare` left `${BRAND_DIR}/campaigns/recipes/ready/lucky-and-rippy-s-favorite-dog-food/`
   (assets) — normally the drainer moves it to `published/`. Move or leave.
5. **Stray scratch dirs** (untracked junk from subagent runs): literal
   `$CLAUDE_SCRATCHPAD_DIR/` folders at repo root and under `recipe-publisher/`.
   Safe to delete.
6. **PostToolUse hook noise:** flags `B101 assert` in tests + "import could not be
   resolved" (no venv) on every edit — false positives; worth tightening.

## Useful facts
- WP creds: `WP_URL`/`WP_USER`/`WP_APP_PASSWORD` via `lib.local_env.load_local_env()`
  (from settings.local.json). Never inline secrets.
- SureRank REST meta returns 403 (free-plan REST-auth paywall) — non-blocking.
- Headless render check (`playwright`): healthy post = `#primary` ~1160px,
  `.ast-container` has 1 child. Broken = ~250px, 3 children.
- recipes.db at `${BRAND_DIR}/data/recipes.db`; back up before edits.
