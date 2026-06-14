# Current Focus — FB groups moved to a DB; recipe flow decoupled into DB-polling workers

_Last updated: 2026-06-14 (session)_

## Latest: FB groups JSON → `groups.db` (brand→groups table) + live scout run

Moved FB groups out of `groups_tracker.json` (flat `list[dict]`) into a real
brand→groups SQLite DB and **fully cut over** all ~12 consumers, so the DB is the
single source of truth (no JSON↔DB drift). User's ask: _"brand and its groups, each
group with details + status; the worker reads from the DB and writes status back."_

- **New module `lib/groups_db/`** (mirrors `recipe_db` conventions): `schema.sql`
  (`brands` 1-row + `fb_groups` FK→brand, indexes on status/brand_id), `db.py`
  (`resolve_groups_db_path()` → `${BRAND_DIR}/data/db/groups.db`, `connect`/`migrate`
  + additive `_ensure_columns`), `models.py` (`GroupStatus`, `PostingMode`,
  `group_id_from_url`), `repository.py` (`GroupsRepository`: `ensure_brand`,
  `upsert_group`, `save_all`/`load_all`, `set_status`/`set_posting_mode`/`append_note`,
  `_row_to_dict` w/ `extra` JSON for unmodeled keys → round-trip fidelity), and a
  drop-in compat layer in `__init__.py` (module-level `load_all`/`save_all`/`get_by_url`/
  `set_status`/… each open→op→close) so the cutover was one line per read/write site.
- **Cutover** (`json.loads(TRACKER.read_text())`→`groups_db.load_all()`,
  `TRACKER.write_text(...)`→`groups_db.save_all(...)`): the worker `fb_group_post.py`
  + `fb_group_note.py`, `fb_group_enrich.py`, `fb_groups_posting_scan.py`,
  `fb_notification_scan.py`, `fb_pending_posts_check.py`, `lib/group_discovery/state.py`,
  `lib/group_warmup.py`, `lib/engagement/adapters/facebook.py`, `api/approval_api.py`
  (GET `load_all`, PUT `set_status`/`set_posting_mode`), `api/flow_helpers.py`.
- **Migrated** the real tracker via `lib.groups_db.migrate_from_json --apply` (copy-only,
  JSON kept). Old `groups_tracker.json` + backups **archived** (not deleted, additive rule)
  to `dogfoodandfun/data/_archive_pre_groupsdb/`. `pending_groups.json` left in place
  (API still merges it for not-joined-yet candidates).
- **Approval removed** from `fb_group_scout.py` — the interactive stdin prompt + dead
  `parse_approve_arg` deleted; `get_user_approval(candidates, budget, selection="all")`
  is now non-interactive (auto-approves up to the daily cap).
- **Live scout run** (`fb_group_scout --force`, user-authorized outward action): joined
  10 groups from the pre-approved queue — **6 joined immediately** (public), **4
  join_requested** (private, pending admin). Daily cap (10) now exhausted. New statuses
  written straight to `groups.db`. **DB now: 30 joined / 10 join_requested / 6 not_joined_yet.**
- **API restarted** — the running instance predated the cutover (still read the archived
  JSON → UI showed 0 joined). Fresh `python -m api.approval_api` on **:5001** now serves
  the DB. Relaunch: `cd social-automation && BRAND_DIR=…/dogfoodandfun .venv/bin/python -m api.approval_api`.
- **Verified:** 6 `tests/test_groups_db.py` pass (brand seed+FK, round-trip incl `extra`/notes,
  idempotent upsert, set_status/posting_mode, append_note, list_groups filter); round-trip
  parity vs JSON confirmed; live join run exit 0; API endpoint returns total=46 with the
  status counts above.

### Next session (groups DB)
1. The 4 pending private requests flip to **joined** automatically once admins approve —
   `fb_notification_scan` writes the status back to the DB.
2. 6 groups still "not joined yet" stay queued; next scout run (cap 10/day) picks them up.
3. Once the DB path is proven over a few live runs, the archived JSON can be removed.

---

## prepare.py monolith → 4 independent DB-polling workers (uncommitted)

Replaced the coupled `recipe-publisher/prepare.py` (which built WP+PDF+images+reel+
captions in one call, tracking state only in a folder `status.json` the DB never saw)
with **four independent workers** under `recipe-publisher/workers/`, each polling the
recipe DB to decide its own work. User's ask: _"each step looks at the DB for its
indication; not one big pipeline."_

- **Per-artifact DB markers** — additive cols on `recipes` (`recipe_db/db.py` `_ADDED_COLUMNS`
  + `schema.sql` + `models.py` + `repository.py` setters/deserialize): `wp_post_id`, `pdf_url`,
  `slides_created_at`, `slides_count`, `reel_created_at`, `audio_ready_at`, `social_published_at`.
  Each worker's predicate is `(prerequisite filled) AND (my output empty)` → independent + idempotent.
  No shared linear enum. `content_status` (10-phase pipeline) left **untouched/dormant**.
- **Workers** — `python -m workers.worker_<name>`, each `--apply/--dry-run/--limit/--seed/--health-check`:
  - **A wp_pdf** — `dog_safe & not wp_url` (or `wp_post_id & not pdf_url`) → WP draft + PDF card;
    also writes `metadata.json`+`ig/fb_caption.txt` (publish inputs for D). Self-heals PDF. Replaces deleted `publish_wp_pdf_batch.py`.
  - **B post_images** — `wp_url & not slides_created_at` → carousel gen ONCE → saves `slides/`
    (badged post) + `reel_src/` (un-badged reel frames). Keeps the badge OFF the reel.
  - **C reel** — `slides_created_at & not reel_created_at` → `compose_reel(reel_src)` → `source.mp4`.
  - **D publish** — audio-detect pre-pass (`reel & not audio_ready` → finds `audio.mp3` → `set_audio_ready`),
    then `reel & audio_ready & not social_published` → wraps `scripts.publish_prepared.publish_one(skip_pdf=True)`;
    records `social_published_at` + IG/FB badge urls.
  - Shared: `workers/_base.py` (CLI + `SingletonLock` + per-row isolation + `pre_apply_fn` hook),
    `workers/_folder.py` (campaign-folder resolver w/ ready→published fallback, Recipe rehydration,
    save/load frames, badge path).
- **Earlier this session:** Nalla-approved **badge** on the carousel POST hero only (top-right,
  replaces the @handle pill; reel keeps the pill) via `text_overlay.apply_image_badge` +
  `carousel.generate_post_and_reel_slides`. **All 21 launchd crons disabled** (`launchctl bootout`+`disable`; plists kept).
- **Deleted (superseded):** `prepare.py`, `scripts/publish_wp_pdf_batch.py`, `generators/lyrics_drafter.py` (dead).
  `recipe_publisher.py` `--prepare` branch removed (kept the file — `run_report.py` imports its `RunResult`).
  **Left (orphaned but tested):** `generators/campaign_assembly.py`, `generators/step_images.py`.
- **Verified:** 24 worker tests + 178 recipe-publisher tests pass; ruff clean on all worker code;
  A→B→C→D handoff demoed on a sandbox DB copy (each marker lights up exactly the next worker; D
  waits on the audio gate). 6 pre-existing `test_instagram`/`test_drafter` failures are unrelated.
  **No worker has run `--apply` live yet** (nothing published).

## Next session
1. **Apply the chain on one real recipe** (outward-facing): `worker_wp_pdf --apply --limit 1`
   → `worker_post_images --apply` → `worker_reel --apply` → drop `audio.mp3` → `worker_publish --apply`.
2. **Add 4 launchd plists** (one per worker) — held off; all crons currently disabled.
3. Optionally delete `campaign_assembly`/`step_images` (+ their tests) if the artifact flow no longer needs them.

---

## Previous: IG/FB engagement loops split + IG caps raised (2026-06-13)

Refactored the outbound-engagement system so **Instagram and Facebook run as fully
independent loops**, and raised IG to **20 likes / 10 comments per day**.

- **IG caps 20/10** at both enforcement layers: `profiles/instagram.json` (→ rebuilt
  `data/rate_limits.json`) + `dogfoodandfun/config.json` (read by `EngagementPolicy`).
  Also mirrored in `social-automation/config.json`, both SKILL.md files, and `CLAUDE.md`.
- **Per-platform queues:** new `instagram_comment_queue.json` / `facebook_comment_queue.json`
  in `lib/config.py` `BrandPaths`; `ig_scan.py`/`fb_scan.py` repointed. Existing
  `comment_queue.json` migrated **copy-only** (source untouched: 155 entries; IG=41, FB=106).
- **Independent posting loops:** `comment_approver.py` + `comment_poster.py` take
  `--platform` (new `lib/comment_queue_routing.py`: queue + per-platform re-run-guard key).
  Profiles now define `ig-comment-approver→ig-comment-poster`, `fb-comment-approver→fb-comment-poster`;
  the old combined `comment-composer` skill flow (legacy Telegram path; engagement is now
  Phase-3 autonomous) was removed and its stale plist pruned. Legacy approver/poster scoped
  to `--platform wordpress`. `launchd_plists.py` gained `args` support (watchdog preserved).
- **Verified:** schedule.json shows no cross-platform deps; 190 engagement/config/launchd
  tests pass (updated 3 spec tests to the new 20 cap; added routing + launchd-args tests).
  Pre-existing 5 failures in `tests/lib/campaigns/` are unrelated to this work.
- **Not done (live):** scanners drive a real browser, so no live scan/post was run
  (verification was dry/code-trace per request). Plists are regenerated on disk but **not
  reloaded** into launchd — run `launchctl` reload (or `profiles_build install --apply`) to activate.

---

## Previous: recipe-pipeline extension (10 phases) — COMMITTED + brand DB enriched

Built and committed the full **recipe content/publish pipeline** as 10 sequential
vertical slices over `recipes.db`, each with a DB schema update, structured JSON
logging (`lib.observability`), and a checkpoint gate (`pipeline/checkpoint.py`).
Everything publish-related is **dry-run / draft-gated** — nothing goes live. Then
enriched the real `dogfoodandfun` DB with phases 1–2 and wired it all to the UI.

## Git state
- **Branch:** `feat/recipe-pipeline-extension` (off `recipe-linebreak-fix`).
- **Commit `d070433`** — "feat(recipe-pipeline): 10-phase content/publish pipeline
  extension" — 39 files, +3619/−82. Contains all phase modules, recipe_db schema/
  model/repo changes, API endpoints, frontend wiring, tests, and the demo script.
- **Uncommitted follow-ups (on disk, survive a session clear):**
  1. Affiliate-products **drawer section** — `frontend/src/pages/RecipeLifecycle.tsx`
     (`AffiliateProductsSection`) + `RecipeDrawer.tsx` renders it under Publishing.
  2. `pipeline/rate_limiting.py` — `DEFAULT_DAILY_CAPS` tightened to
     `{ig:1, fb:1, pinterest:1}` (user edit).
  3. **Prior-session recipe-card WIP** (NOT mine, still uncommitted): `generators/`,
     `prompts/recipe_system.md`, `publishers/wordpress.py`, `templates/recipe_card/`,
     prior `scripts/` (publish_wp_pdf_batch, regen_hero_images, render_card_from_db).
     Left untouched/unstaged deliberately.

## Phases (all complete + tested) — `recipe-publisher/pipeline/`
1 seasonal_selection · 2 affiliate_matching (Amazon, `lib.recipe_products`) ·
3 content_generation (injected `DraftProducer`; prod wraps `generators.recipe`) ·
4 pending_review · 5 approval (human gate; API+UI) · 6 dedup_check (external
`state/published_recipes.json`, not the table's unique keys) · 7 rate_limiting ·
8 publishing (composes 6/7/9; dry-run default; live `PlatformPublisher` UNWIRED) ·
9 retry · 10 analytics (local outcome log). Reusable: `checkpoint.py`, `_cli.py`.
5 new `recipes` columns: `season_tags`, `affiliate_products`, `generated_content`,
`content_status` (ContentStatus lifecycle), `publish_results`.

## What ran against the REAL brand DB (`BRAND_DIR=…/dogfoodandfun`)
- Backup: `data/recipes.db.bak-20260612-170137` (restore to undo enrichment).
- **Phase 1** seasonal_selection: season=summer, `season_tags` on 7 recipes (rest
  all-season), 22 summer-eligible.
- **Phase 2** affiliate_matching: 76 Amazon product links across all 27 recipes.
- content_status still `none` for all (phases 3–8 NOT run on real data).

## Verification
- 75 pipeline+recipe_db tests + 8 API route tests pass; ruff clean; frontend tsc 0.
- API endpoints verified live over HTTP (`:5001`): season/content_status filters,
  `/recipes/analytics`, `?season=monsoon`→400, new fields served on all rows.
- Full recipe-publisher suite: 149 pass, **7 pre-existing failures** in
  test_drafter/test_instagram/test_text_overlay (env-dependent, outside this diff).

## Running processes this session (may still be up)
- API: `.venv/bin/python -m api.approval_api` on **:5001** (PID 12216) — serves
  enriched data. Frontend: Vite on **:5173** (PID 27983), `@dogfoodandfun/approval-ui`.
- Client base URL: `http://127.0.0.1:5001/api/v1` (no `.env` override; only `.env.example`).

## UI — where the pipeline data shows
Recipes page: season dropdown (server filter), **🔗 N affiliate products** count
under each name, content-status badge + Approve/Reject (only once `content_status`
advances), analytics summary in the header. Detail drawer: **Affiliate products
(Amazon)** section (names + ASIN→Amazon links).

## Next session
1. **Commit the follow-ups** (affiliate drawer section + rate-cap tweak) on
   `feat/recipe-pipeline-extension`; consider opening a PR.
2. **Content generation on real data**: `content_generation --health-check` →
   `--dry-run` → for real (needs Gemini/`VOICE_PROVIDER` key; each DB recipe must
   match a seed in `seeds/seeds.json` — verify coverage or add a non-seed path).
   Then recipes reach `generated`→`pending` and the lifecycle/Approve UI lights up.
3. **Wire a live `PlatformPublisher`** for phase 8 (assemble Recipe + carousel/image
   assets from `generated_content`; reuse `publishers/instagram|facebook|pinterest`),
   behind explicit `--no-dry-run`. Pinterest API still Trial-blocked.
4. Move per-platform caps + analytics knobs to `config.json` (currently constants).

## Useful facts
- Engine code → `social-automation/recipe-publisher`; brand data → `dogfoodandfun/`
  (BRAND_DIR), brand = dogfoodandfun. `dogfoodandfun/recipe-publisher` is empty.
- Phase code bridges to `lib.*` via `pipeline/_cli.py` (like `api/recipes_api.py`).
- Demo: `BRAND_DIR=… $PY scripts/run_pipeline_demo.py [--limit N] [--dry-run]
  [--keep-db]` — walks a SANDBOX COPY through all 10 phases offline (real DB untouched).
- PostToolUse hook type-checks a /tmp copy w/o the project venv → spurious
  import/isort/`S101` errors; the project ruff/pytest/tsc are authoritative. Keep
  `# pyright: reportMissingImports=false` on cross-root modules, `# ruff: noqa: S101`
  on `recipe-publisher/tests/*`.
- See memory `project_recipe_pipeline_infra` for the lib/ reuse map.
