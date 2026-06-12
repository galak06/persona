# Current Focus — recipe-pipeline extension (10 phases)

_Last updated: 2026-06-12 (session)_

Built the full **recipe content/publish pipeline** as 10 sequential vertical
slices on top of `recipes.db`, each with DB schema updates, structured JSON
logging (`lib.observability`), and a checkpoint-validation gate
(`pipeline/checkpoint.py`). Everything publish-related is **dry-run / draft
gated** — nothing goes live. Nothing committed yet.

## Phases (all complete + tested)
New package `recipe-publisher/pipeline/`; one module per phase + reusable
`checkpoint.py` gate and `_cli.py` (path-bridge + structlog).

1. **seasonal_selection.py** — `current_season`/`infer_seasons`/`in_season`; picks
   season-appropriate recipes. DB: `season_tags`.
2. **affiliate_matching.py** — reuses `lib.recipe_products.pick_products` (Amazon
   Associates), matches on name+ingredients. DB: `affiliate_products`.
3. **content_generation.py** — injected `DraftProducer` (prod adapter wraps
   `generators.recipe.generate_recipe`); dry-run skips API calls. DB:
   `generated_content`, `content_status`.
4. **pending_review.py** — stages complete `generated` drafts → `pending`.
5. **approval.py** — human gate `pending`→`approved`/`rejected` (API + UI buttons).
6. **dedup_check.py** — rejects approved recipes whose slug is already published
   (external `state/published_recipes.json` ∪ DB `published`). NOTE: keys off
   external history, not the table's unique id/content_hash (which can't collide).
7. **rate_limiting.py** — pure per-platform daily-cap gate (ig=5, fb=10, pin=10).
8. **publishing.py** — orchestrates dedup→rate→retry→publish to IG/FB/Pinterest;
   `dry_run=True` default; live `PlatformPublisher` adapter intentionally NOT wired
   (inject one for real sends). DB: `publish_results`.
9. **retry.py** — `retry_call` transient-retry helper used by phase 8.
10. **analytics.py** — rolls up `publish_results` into platform/status counts
    (local outcome log only). API: `GET /recipes/analytics`.

## Layers touched (per slice)
- DB: `recipe_db/schema.sql` + `db.py:_ADDED_COLUMNS` (5 new columns) +
  `models.RecipeRow` (+`ContentStatus`) + `repository` setters/queries.
- API: `recipes_api` season/content_status filters, `approve`/`reject`,
  `analytics`; `recipe_schemas` new models. `/recipes/analytics` declared BEFORE
  `/recipes/{id}` to avoid path capture.
- Frontend: `api/recipes.ts` (filters + approve/reject/analytics clients);
  `Recipes.tsx` season dropdown + affiliate/lifecycle badges; new
  `RecipeLifecycle.tsx` (status badge, approve/reject buttons, analytics bar).

## Verification
- **75 pipeline+recipe_db tests pass**; **8 API route tests pass**. ruff clean on
  all new/changed py; frontend `tsc` 0 errors. All 5 runnable phase CLIs
  (`--dry-run`/`--health-check`) exit 0 with checkpoint + structured logs.
- Full recipe-publisher suite: 149 passed, **7 pre-existing failures** in
  `test_drafter`/`test_instagram`/`test_text_overlay` (LLM-SDK & respx mocks, PIL
  pixels) — outside this work's diff, environment-dependent.

## Open items / next session
1. **Commit** (all uncommitted; on branch `recipe-linebreak-fix`). Consider a
   dedicated branch + PR per the vertical-slice rule.
2. **Wire a live `PlatformPublisher`** for phase 8 (assemble Recipe + carousel/
   image assets from `generated_content`; reuse `publishers/instagram|facebook|
   pinterest`). Keep behind explicit `--no-dry-run`. Pinterest API still
   Trial-blocked (see memory).
3. **Content producer**: `SeedDraftProducer` needs DB rows to map to seeds; verify
   seed_id↔recipe.id coverage or add a non-seed drafter path.
4. Per-platform caps + analytics could move to config.json (currently constants).

## Useful facts
- New phase code in `recipe-publisher/pipeline/`; bridges to `lib.*` via
  `pipeline/_cli.py` (same pattern as `api/recipes_api.py`).
- PostToolUse hook type-checks a /tmp copy w/o project venv → spurious
  import/isort/`S101` errors; the **project** ruff/pytest are authoritative. Keep
  `# pyright: reportMissingImports=false` on cross-root modules and
  `# ruff: noqa: S101` on `recipe-publisher/tests/*` (kept for the hook).
- See memory `project_recipe_pipeline_infra` for the lib/ reuse map.
