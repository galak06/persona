# Current Focus — engagement pipeline decomposed (PR #36) · Instagram-only

_Last updated: 2026-07-21 (session)_

**Naming note (2026-08-03):** the flow referred to below as `ig-scanner` was
renamed to `ig-engager` (it likes AND comments in one pass, so "scanner"
undersold it). Entries below are left as originally written — historical
record, not current naming.

## Latest (2026-07-21): engagement pipeline decomposition — MERGED to main (PR #36)

- **`lib/engagement/pipeline.py` is now orchestration-only (~275 lines).** Extracted
  single-responsibility modules (each under the 300-line cap): `collaborators.py`
  (collaborator Protocols), `scan_results.py` (`ScanReport` + per-step outcomes),
  `post_processor.py` (one post visit: dedup → score → like → comment → mark-seen),
  `like_step.py`, `inline_comment.py` (IG single-pass draft-and-post), `queueing.py`
  (FB two-stage cherry-pick + queue), `adapters/instagram_session.py` (Playwright
  session lifecycle). Plus new `lib/scan_dedup.py` (iterate-once reconciliation across
  the two dedup stores). New tests: `test_pipeline_{dry_run,inline_comment,retry}.py`,
  `test_scan_dedup.py`.
- **Two architectural modes now cleanly split:** **IG = single-pass** —
  `scripts/ig_scan.py` is THE entrypoint (scores + likes + comments in one visit; no
  `ig_comment.py` handoff for IG); **FB = two-stage** — scan+queue, drained by
  `fb_comment.py`.
- **Also merged — `fix(db)`:** `lib/db_pool.py` now raises a clear `RuntimeError` at
  pool-creation when `DATABASE_URL` is unset (was a silent credential-less `DEFAULT_DSN`
  fallback that died later with `PoolTimeout: no password supplied` on host runs).
  New `tests/test_db_pool.py`.

### Current focus: Instagram ONLY (as of 2026-07-20)
- FB engagement is parked. `scan_dedup` is deliberately NOT yet wired into `fb_scan.py`
  — that's a follow-up for when FB focus resumes.

### Architecture facts worth remembering
- Comment "agent" = **Gemini 2.5 Flash** (`lib/gemini_client.py`, env
  `GEMINI_REPLY_MODEL`). One structured call returns `{engage, comment, reason}` =
  draft + self-approval in a single shot (no human gate; `engage:false` = decline).
  temp 0.7, `thinkingBudget=0`.
- Relevance **scoring is heuristic** (keyword weights + meta signals in
  `comment_generator.py::score_relevance`, plus `adjust_score` per adapter) — NOT an
  LLM. Dedup is hash/text-based (no LLM). The ONLY LLM touchpoint in the per-post path
  is the drafting step.

### Verified locally
- `python scripts/ig_scan.py --dry-run` runs headless against the live IG account
  (scan-only — no like/comment/dedup/last-run written). Logs reviewed in Grafana:
  query `{flow="ig_scan"}` at http://localhost:3001 (admin/admin).
- Gotcha for manual host runs: must pass explicit `DATABASE_URL`, and logs only reach
  Grafana if appended to `brands/<brand>/logs/cron_ig_scan.log` (what Promtail tails) —
  the worker container does this automatically.

### Open follow-ups
- Harden the FB comment-box submit selector in `lib/fb/comment_post.py` (falls back to
  Enter + "comment box not found") — **FB, parked**.
- Wire `scan_dedup` into `fb_scan.py` when FB focus resumes — **parked**.
- Fill the diet/gear/experience TODOs in `data/config/nalla_facts.md` so comments can be
  specific (until then drafts stay correctly general).

---

## 2026-06-15: grounded comment drafts + both platforms run live

- **Fabrication fix (brand accuracy).** Engagement comments were inventing false
  specifics (e.g. "we've fed raw for over a year") because `_VOICE_RULES` told the
  model to "be specific with numbers" and it had no true facts. Two-part fix:
  (A) **no-fabrication guardrail** in `_VOICE_RULES` (`lib/reply_drafter.py`) — forbids
  invented diets/durations/ages/gear, fall back to general + curious; applies to ALL
  drafting. (B) **facts grounding** — `lib/draft_helper.py` `_nalla_facts()` loads
  `${BRAND_DIR}/data/config/nalla_facts.md` (lru_cached) and injects it as a "NALLA
  FACTS" block. **Owner must fill** the diet/gear/experience TODOs in that file to
  make comments specific; until then they stay correctly general. Covers BOTH FB +
  IG (shared `draft_helper`). See memory [[project_nalla_facts_grounding]].
  - NOT yet wired into `reply_drafter`'s own prompts (reply-follower/auto-drafter) —
    they get the guardrail but not the facts block. Open follow-up.
- **Caps → 15.** FB and IG `comments_per_day` both bumped 10→15 (profiles + regenerated
  `data/rate_limits.json`); `test_policy` updated. FB `group_visit` is 15 too.
- **Operations + Flow Explorer fully reflect fb-comment/ig-comment** — `_LABEL_TO_FLOW`
  + `_LABEL_TO_LOG` (`api/schedule_state.py`), `flow_descriptions.py`, and the
  installed `~/Library/LaunchAgents` plists (added `{fb,ig}-comment`, removed 4 stale
  `*-approver/poster`; all unloaded, crons still disabled).
- **Live runs done:** FB — joined 30 groups, scanned, posted 3 comments (2 box-not-found
  failures = selector brittleness, still open). IG — scanned (26 hashtags, 20 likes, 10
  queued), posted comments (Submit button works on IG, unlike FB's Enter fallback). A
  `ig_comment.py --force` run for the remaining ~9 was in flight at session end.

---

## IG comment flow → 2 single actions (shared core with FB)

Applied the FB scanner+commenter split to Instagram, and — to avoid duplicating
the ~250-line drain loop — extracted the shared core into
**`lib/engagement/commenter.py`** (`CommenterSpec` + `run_commenter`/`main_for`).
Both `fb_comment.py` and `ig_comment.py` are now ~60-line thin specs.
(Note: PR #36 above later reversed IG to single-pass — `ig_scan.py` is THE IG
entrypoint now; the two-step below is retained as FB/history context.)

- **`lib/engagement/commenter.py`** — platform-agnostic drain loop: re-run guard,
  pending filter (`already_commented` only), draft-at-post-time, Playwright post,
  dedup + rate + engagements.db record, pacing. Parameterized by `CommenterSpec`
  (platform, session/queue/log paths, guard key, home_url, login markers,
  target_field, `draft_fn`, `post_fn`).
- **`scripts/comment_poster.py`** → now **WP-only** (FB + IG branches removed,
  `post_comment_ig` moved out, no browser launch). `comment_approver.py` likewise
  WP-only in the flow.
- **Profiles/schedule:** `profiles/instagram.json` `ig-comment-approver` +
  `ig-comment-poster` → one `ig-comment` (deps `ig-scanner`); regenerated
  `schedule.json`/`rate_limits.json` (DAG 19 flows, engine `--check` exit 0).
- **Flow Explorer:** updated all 3 sources it reads — `api/flow_descriptions.py`
  (guide), `api/schedule_state.py` `_LABEL_TO_FLOW` (added fb-comment + ig-comment →
  engagement-comment), and the installed launchd plists. Regenerated brand plists,
  synced `~/Library/LaunchAgents`: **added** `com.dogfoodandfun.{fb,ig}-comment.plist`,
  **removed** the 4 stale `{fb,ig}-comment-{approver,poster}.plist`. All plists remain
  **disabled/unloaded** (0 in `launchctl list`) — file sync only.
- **Verified:** new `tests/test_commenter.py` + `test_ig_comment.py` + rewritten
  `test_fb_comment.py` + updated `test_ig_scan` record shape; **156 scoped tests pass;
  ruff clean; drift guard passes**; API restarted, `/flows/guide` shows all 6 jobs.

---

## engagements.db — published posts + comments, DB→API→UI

Full vertical slice: a queryable history of every published post + comment
(previously only in `engagement_log.jsonl` + the queue JSON). New
`lib/engagements_db/` (mirrors `groups_db`): `engagements` table at
`${BRAND_DIR}/data/db/engagements.db` — one row per publish (platform, kind,
status, target, permalink, content, source_ref, posted_at), upsert-keyed by
`dedup_id(platform, kind, ref)` so retries/failures collapse. `record_publish()`
is **defensive** (swallows DB errors so logging never breaks a publish).

- **Writers wired:** `scripts/fb_comment.py` (comment posted + both failure
  paths), `scripts/fb_group_post.py` (FB group link_post/reel after last_post),
  `scripts/publish_prepared.py` (IG reel, FB reel, FB page_post — recipe pipeline,
  still dormant/dry-run so no live rows yet).
- **API:** new `api/engagements_api.py` router → `GET /api/v1/engagements`
  (`?platform=&kind=&status=&limit=`) + posted-only `counts`; included in
  `approval_api.py`. API restarted on :5001.
- **UI:** `frontend/src/pages/Published.tsx` (platform filter tabs, counts chips,
  table with outbound links) + `api/engagements.ts` (manual types — openapi still
  bypassed) + route `/published` + SideNav "Published" entry (Engagement section).
- **Backfilled** 48 FB comment rows from the queue (23 posted incl. today's 3, 25
  failed). Live API confirmed serving them.
- **Verified:** `tests/test_engagements_db.py` (5 tests) + 13 scoped pass; ruff clean;
  **frontend tsc 0 errors** (the old stale-openapi redness is gone).

---

## FB comment flow → 2 single actions (scanner + commenter)

Broke the FB outbound-comment flow from scan-and-draft-in-one + auto-approver +
poster into **two single-responsibility actions**, drafting at POST time. Likes stay
in the scanner; comments are **one sentence (~15-25 words)** grounded in the live post.

- **Action 1 · `fb-scanner` (scan only)** — `scripts/fb_scan.py` passes `drafter=None`
  to the shared pipeline; FB queue records carry an empty `draft_comment`. IG/WP
  scanners keep drafting inline.
- **Action 2 · `fb-comment` (`scripts/fb_comment.py`)** — drains the FB queue's
  `status="pending"` items: drafts a short reply at post time, posts via Playwright,
  records dedup/rate/log/queue-status. Re-run guard `comment_composer_facebook`, cap
  5/day. CLI: `--dry-run/--force/--limit/--health-check`.
- **Short, post-grounded draft** — `lib/draft_helper.py`: shared call→validate→retry
  core (`_draft_validated`) + `draft_short_comment_for_post` (one sentence;
  `validate_voice` enforces trailing `?`, ≥40 chars, specificity, first-person).
- **No duplication** — Playwright `post_comment_fb` extracted into
  `lib/fb/comment_post.py`; removed from `scripts/comment_poster.py`.
- **Wiring** — `profiles/facebook.json`: `fb-comment-approver` + `fb-comment-poster`
  replaced by one `fb-comment` flow (depends_on `fb-scanner`); regenerated
  `schedule.json` + `rate_limits.json` (DAG valid, 20 flows; crons still disabled).
- **Tests** — `tests/test_draft_helper.py` (short variant) + `tests/test_fb_comment.py`
  + updated `test_fb_scan_record_shape`. **112 + 151 scoped tests pass; ruff clean.**

---

## FB groups JSON → `groups.db` (brand→groups table) + live scout run

Moved FB groups out of `groups_tracker.json` (flat `list[dict]`) into a real
brand→groups SQLite DB and **fully cut over** all ~12 consumers, so the DB is the
single source of truth (no JSON↔DB drift).

- **New module `lib/groups_db/`** (mirrors `recipe_db` conventions): `schema.sql`
  (`brands` 1-row + `fb_groups` FK→brand), `db.py` (`resolve_groups_db_path()` →
  `${BRAND_DIR}/data/db/groups.db`, `connect`/`migrate` + additive `_ensure_columns`),
  `models.py` (`GroupStatus`, `PostingMode`, `group_id_from_url`), `repository.py`
  (`GroupsRepository`: `ensure_brand`, `upsert_group`, `save_all`/`load_all`,
  `set_status`/`set_posting_mode`/`append_note`, `_row_to_dict` w/ `extra` JSON for
  round-trip fidelity), plus a drop-in compat layer in `__init__.py` so the cutover was
  one line per read/write site.
- **Cutover:** the worker `fb_group_post.py` + `fb_group_note.py`, `fb_group_enrich.py`,
  `fb_groups_posting_scan.py`, `fb_notification_scan.py`, `fb_pending_posts_check.py`,
  `lib/group_discovery/state.py`, `lib/group_warmup.py`,
  `lib/engagement/adapters/facebook.py`, `api/approval_api.py`, `api/flow_helpers.py`.
- **Migrated** via `lib.groups_db.migrate_from_json --apply` (copy-only). Old
  `groups_tracker.json` + backups **archived** to `dogfoodandfun/data/_archive_pre_groupsdb/`
  (additive rule). `pending_groups.json` left in place (API merges not-joined-yet candidates).
- **Approval removed** from `fb_group_scout.py` — `get_user_approval(...)` is now
  non-interactive (auto-approves up to the daily cap).
- **Live scout run** (`fb_group_scout --force`): joined 10 groups — 6 immediately
  (public), 4 join_requested (private, pending admin). **DB now: 30 joined /
  10 join_requested / 6 not_joined_yet.** API restarted on :5001 to serve the DB.
- **Verified:** 6 `tests/test_groups_db.py` pass; round-trip parity vs JSON confirmed;
  live join run exit 0; API endpoint returns total=46 with the status counts above.

---

## prepare.py monolith → 4 independent DB-polling workers

Replaced the coupled `recipe-publisher/prepare.py` (which built WP+PDF+images+reel+
captions in one call, tracking state only in a folder `status.json` the DB never saw)
with **four independent workers** under `recipe-publisher/workers/`, each polling the
recipe DB to decide its own work: _"each step looks at the DB for its indication."_

- **Per-artifact DB markers** — additive cols on `recipes`: `wp_post_id`, `pdf_url`,
  `slides_created_at`, `slides_count`, `reel_created_at`, `audio_ready_at`,
  `social_published_at`. Each worker's predicate is `(prerequisite filled) AND (my
  output empty)` → independent + idempotent. `content_status` (10-phase) left dormant.
- **Workers** — `python -m workers.worker_<name>` (`--apply/--dry-run/--limit/--seed/--health-check`):
  - **A wp_pdf** — `dog_safe & not wp_url` → WP draft + PDF card; also writes
    `metadata.json`+captions (publish inputs for D). Self-heals PDF.
  - **B post_images** — `wp_url & not slides_created_at` → carousel gen ONCE → `slides/`
    (badged post) + `reel_src/` (un-badged reel frames). Badge OFF the reel.
  - **C reel** — `slides_created_at & not reel_created_at` → `compose_reel` → `source.mp4`.
  - **D publish** — audio-detect pre-pass, then `reel & audio_ready & not social_published`
    → wraps `publish_prepared.publish_one(skip_pdf=True)`; records `social_published_at`.
  - Shared: `workers/_base.py` (CLI + `SingletonLock` + per-row isolation + `pre_apply_fn`),
    `workers/_folder.py` (campaign-folder resolver, Recipe rehydration, badge path).
- **Deleted (superseded):** `prepare.py`, `scripts/publish_wp_pdf_batch.py`,
  `generators/lyrics_drafter.py`. **Left orphaned but tested:** `campaign_assembly.py`,
  `step_images.py`.
- **Verified:** 24 worker tests + 178 recipe-publisher tests pass; ruff clean; A→B→C→D
  handoff demoed on a sandbox DB copy. **No worker has run `--apply` live yet.**

---

## Archived context (pre-July, condensed)

- **IG/FB engagement loops split + caps (2026-06-13).** Made Instagram and Facebook
  fully independent outbound loops; raised IG to 20 likes / 10 comments/day at both
  enforcement layers (`profiles/instagram.json` + `config.json`). Per-platform queues
  (`instagram_comment_queue.json` / `facebook_comment_queue.json`), `--platform`-scoped
  approver/poster, legacy combined `comment-composer` flow removed. Later superseded by
  the scanner/commenter split and the PR #36 decomposition above.
- **Recipe-pipeline extension — 10 phases, COMMITTED (`feat/recipe-pipeline-extension`,
  commit d070433).** Full content/publish pipeline over `recipes.db` as sequential
  vertical slices, each with schema update + JSON logging + checkpoint gate; everything
  publish-related is dry-run / draft-gated. 5 new `recipes` cols (`season_tags`,
  `affiliate_products`, `generated_content`, `content_status`, `publish_results`). Ran
  phases 1–2 on the real brand DB (season_tags on 7 recipes; 76 Amazon links across 27
  recipes); phases 3–8 NOT run on real data (`content_status` still `none`). Live
  `PlatformPublisher` for phase 8 remains UNWIRED; Pinterest still Trial-blocked.
  Operationally superseded by the 4-worker DB-polling model (section above). See memory
  `project_recipe_pipeline_infra` for the lib/ reuse map.
