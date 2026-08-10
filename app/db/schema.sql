-- Persona — local Postgres schema.
--
-- Self-initializing schema for the self-hosted stack. Mounted at
-- /docker-entrypoint-initdb.d/schema.sql so a fresh `postgres:16` container
-- applies it automatically on first `docker compose up` — no manual dashboard
-- step, no Supabase-specific extensions/RLS policies. All statements are
-- `IF NOT EXISTS`, safe to re-run.
--
-- Scope: only the tables consumed by the modules migrating off Supabase this
-- stage — groups_db, engagements_db, worker_db, schedule_db, dedup_pg,
-- published_content, content_ideas (lib/ideas_db.py, migrated 2026-08 after
-- the Supabase project's DNS went permanently unreachable). `recipes_db`
-- (recipes, raw_scrapes) and `oauth_tokens` stay on whatever they use today
-- and are intentionally NOT included here.
--
-- Column names/types/defaults are a lift-and-shift from
-- scripts/create_supabase_schema.sql, not a redesign.

-- ────────────────────────────────────────────────────────────────────────────
-- schedule_tasks (from schedule.db)
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS schedule_tasks (
    id                  TEXT        PRIMARY KEY,
    title               TEXT,
    description         TEXT,
    order_num           INTEGER     DEFAULT 0,
    script              TEXT,
    skill               TEXT,
    args                JSONB       DEFAULT '[]',
    timeout_minutes     INTEGER,
    depends_on          JSONB       DEFAULT '[]',
    requires_approval   INTEGER     DEFAULT 0,
    requires_browser    INTEGER     DEFAULT 0,
    re_run_guard        INTEGER     DEFAULT 1,
    output_file         TEXT,
    schedule            JSONB,
    inputs              JSONB       DEFAULT '[]',
    telegram_notify     INTEGER     DEFAULT 0,
    extra               JSONB       DEFAULT '{}'
);

-- Additive (PR2 — Phase A dispatcher): brand-scoped dispatch. Existing rows
-- default to 'dogfoodandfun' (today's only brand) so this migration is a
-- no-op for current data; new brands set their own brand_id going forward.
ALTER TABLE schedule_tasks ADD COLUMN IF NOT EXISTS brand_id TEXT NOT NULL DEFAULT 'dogfoodandfun';
CREATE INDEX IF NOT EXISTS idx_schedule_tasks_brand ON schedule_tasks(brand_id);

-- ────────────────────────────────────────────────────────────────────────────
-- worker_runs (from workers.db)
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS worker_runs (
    worker_label    TEXT    NOT NULL,
    brand           TEXT    NOT NULL,
    status          TEXT    NOT NULL,
    last_run        TEXT    NOT NULL,
    message         TEXT    DEFAULT '',
    PRIMARY KEY (worker_label, brand)
);

-- ────────────────────────────────────────────────────────────────────────────
-- engagements (from engagements.db)
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS engagements (
    id              TEXT    PRIMARY KEY,
    brand_id        TEXT    NOT NULL    DEFAULT '',
    platform        TEXT    NOT NULL,
    kind            TEXT    NOT NULL,
    status          TEXT    NOT NULL    DEFAULT 'posted',
    target_name     TEXT                DEFAULT '',
    target_url      TEXT                DEFAULT '',
    permalink       TEXT                DEFAULT '',
    content         TEXT                DEFAULT '',
    source_ref      TEXT                DEFAULT '',
    error           TEXT                DEFAULT '',
    posted_at       TEXT                DEFAULT '',
    created_at      TEXT,
    updated_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_engagements_platform_kind ON engagements(platform, kind);
CREATE INDEX IF NOT EXISTS idx_engagements_status        ON engagements(status);
CREATE INDEX IF NOT EXISTS idx_engagements_posted_at     ON engagements(posted_at);

-- ────────────────────────────────────────────────────────────────────────────
-- brands + fb_groups (from groups.db)
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS brands (
    id          TEXT    PRIMARY KEY,
    name        TEXT    NOT NULL,
    persona     TEXT    DEFAULT '',
    site_url    TEXT    DEFAULT '',
    created_at  TEXT,
    updated_at  TEXT
);

-- Additive (PR3 — Phase B onboarding): brand-registry fields beyond the
-- minimal {id, name, persona, site_url} shape above. Existing rows (today's
-- only brand, dogfoodandfun, seeded via groups_db.ensure_brand()) pick up
-- these defaults untouched.
ALTER TABLE brands ADD COLUMN IF NOT EXISTS niche               TEXT  DEFAULT '';
ALTER TABLE brands ADD COLUMN IF NOT EXISTS mascot_name         TEXT  DEFAULT '';
ALTER TABLE brands ADD COLUMN IF NOT EXISTS target_audience     TEXT  DEFAULT '';
ALTER TABLE brands ADD COLUMN IF NOT EXISTS keywords            JSONB DEFAULT '{}';
ALTER TABLE brands ADD COLUMN IF NOT EXISTS competitor_accounts JSONB DEFAULT '[]';
ALTER TABLE brands ADD COLUMN IF NOT EXISTS enabled_flows       JSONB DEFAULT '["ig-engager","fb-scanner"]';
ALTER TABLE brands ADD COLUMN IF NOT EXISTS status              TEXT  NOT NULL DEFAULT 'draft';
ALTER TABLE brands ADD COLUMN IF NOT EXISTS brand_dir           TEXT  DEFAULT '';
ALTER TABLE brands ADD COLUMN IF NOT EXISTS extra               JSONB DEFAULT '{}';
-- Additive (PR5 — brand settings): whether this brand's Playwright scanners
-- run with a visible browser window. Existing rows default to TRUE
-- (production-safe, matches `lib.local_env.get_runtime_headless()`'s own
-- fallback), so this migration is a no-op behavior-wise for brands that
-- never customize it.
ALTER TABLE brands ADD COLUMN IF NOT EXISTS headless            BOOLEAN NOT NULL DEFAULT TRUE;
-- Additive (PR6 — fb-group-scout wiring): daily cap on new-group join
-- requests scripts/fb_group_scout.py sends. Existing rows default to 10,
-- matching that script's own pre-PR6 hardcoded JOIN_LIMIT_PER_DAY, so this
-- migration is a no-op behavior-wise for brands that never customize it.
ALTER TABLE brands ADD COLUMN IF NOT EXISTS group_join_limit    INTEGER NOT NULL DEFAULT 10;
CREATE INDEX IF NOT EXISTS idx_brands_status ON brands(status);

CREATE TABLE IF NOT EXISTS fb_groups (
    id                       TEXT    PRIMARY KEY,
    brand_id                 TEXT    NOT NULL    REFERENCES brands(id),
    group_url                TEXT    NOT NULL    UNIQUE,
    group_name               TEXT    DEFAULT '',
    status                   TEXT    NOT NULL    DEFAULT 'join_requested',
    joined_at                TEXT    DEFAULT '',
    rules                    TEXT    DEFAULT '',
    source_notification      TEXT    DEFAULT '',
    privacy                  TEXT    DEFAULT '',
    member_count             TEXT    DEFAULT '',
    posting_mode             TEXT    DEFAULT '',
    self_promo_allowed       TEXT    DEFAULT '',
    category                 TEXT    DEFAULT '',
    notes                    JSONB               DEFAULT '[]',
    last_post_status         TEXT    DEFAULT '',
    last_post_caption        TEXT    DEFAULT '',
    last_post_permalink      TEXT    DEFAULT '',
    last_post_at             TEXT    DEFAULT '',
    last_reel_caption        TEXT    DEFAULT '',
    last_reel_post_at        TEXT    DEFAULT '',
    last_reel_post_permalink TEXT    DEFAULT '',
    last_checked_at          TEXT    DEFAULT '',
    extra                    JSONB               DEFAULT '{}',
    created_at               TEXT,
    updated_at               TEXT
);

CREATE INDEX IF NOT EXISTS idx_fb_groups_status   ON fb_groups(status);
CREATE INDEX IF NOT EXISTS idx_fb_groups_brand_id ON fb_groups(brand_id);

-- ────────────────────────────────────────────────────────────────────────────
-- completed_tasks (from lib/dedup_pg.py -- permanent like/comment dedup)
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS completed_tasks (
    task_type    VARCHAR(50)     NOT NULL,
    platform     VARCHAR(20)     NOT NULL,
    entity_id    VARCHAR(255)    NOT NULL,
    brand        VARCHAR(100)    NOT NULL,
    worker_label VARCHAR(100)    NOT NULL    DEFAULT '',
    meta         JSONB           NOT NULL    DEFAULT '{}',
    completed_at TEXT            NOT NULL    DEFAULT NOW()::TEXT,
    PRIMARY KEY (task_type, platform, entity_id, brand)
);

CREATE INDEX IF NOT EXISTS idx_completed_tasks_brand ON completed_tasks(brand, task_type, platform);
CREATE INDEX IF NOT EXISTS idx_completed_tasks_at    ON completed_tasks(completed_at DESC);

-- ────────────────────────────────────────────────────────────────────────────
-- flow_templates (from profiles/*.json -- brand-agnostic flow catalog read by
-- lib/brand_provisioning.py when onboarding a new brand; seeded/reseeded via
-- scripts/backfill_flow_templates.py)
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS flow_templates (
    id                  TEXT        PRIMARY KEY,
    platform            TEXT        NOT NULL,
    title               TEXT        NOT NULL,
    description         TEXT        DEFAULT '',
    order_num           INTEGER     DEFAULT 0,
    script              TEXT,
    skill               TEXT,
    args                JSONB       DEFAULT '[]',
    depends_on          JSONB       DEFAULT '[]',
    requires_approval   INTEGER     DEFAULT 0,
    approval_channel    TEXT,
    requires_browser    INTEGER     DEFAULT 0,
    re_run_guard        INTEGER     DEFAULT 1,
    output_file         TEXT,
    schedule            JSONB       DEFAULT '{}',
    inputs              JSONB       DEFAULT '[]',
    telegram_notify     INTEGER     DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_flow_templates_platform ON flow_templates(platform);

-- ────────────────────────────────────────────────────────────────────────────
-- published_content (Slice 2 — GSC signal: published-URL <-> GSC-query rows)
-- One row per (brand, wp_url, gsc_query) tuple, refreshed by
-- scripts/backfill_gsc_content.py. brand_id-scoped like engagements; NOT the
-- stale local SQLite recipes.db read by api/recipes_api.py (see plan
-- blocker 4) -- this is the only "published content" source of truth for
-- the GSC signal.
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS published_content (
    id              TEXT                PRIMARY KEY,  -- dedup key: slug of {brand_id}:{wp_url}:{gsc_query}
    brand_id        TEXT                NOT NULL,
    wp_url          TEXT                NOT NULL,
    wp_post_id      TEXT                DEFAULT '',
    gsc_query       TEXT                NOT NULL,
    position        DOUBLE PRECISION    DEFAULT 0,
    impressions     INTEGER             DEFAULT 0,
    clicks          INTEGER             DEFAULT 0,
    ctr             DOUBLE PRECISION    DEFAULT 0,
    fetched_at      TEXT                DEFAULT '',
    created_at      TEXT,
    updated_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_published_content_brand      ON published_content(brand_id);
CREATE INDEX IF NOT EXISTS idx_published_content_brand_url  ON published_content(brand_id, wp_url);
CREATE INDEX IF NOT EXISTS idx_published_content_brand_query ON published_content(brand_id, gsc_query);
CREATE INDEX IF NOT EXISTS idx_published_content_position   ON published_content(position);

-- ────────────────────────────────────────────────────────────────────────────
-- content_ideas (Slice 3 — content-ideator / GSC-scout idea backlog; replaces
-- the Google Sheet "posts" tab and Supabase's content_ideas table now that
-- the Supabase project's DNS is permanently unreachable. `id` is a random
-- UUID generated in Python (lib/ideas_db.py, uuid.uuid4()) at insert time --
-- NOT a deterministic dedup hash like published_content's, because two
-- different ideas can legitimately share the same (topic, brand) text at
-- different points in time; the UNIQUE index below on (lower(topic),
-- brand_id) is what actually prevents duplicate topics, not the primary key.
--
-- Column is named `nalla_context`, NOT `persona_context` as in the old
-- scripts/create_supabase_schema.sql:198-218 -- that file's column name was
-- never what the real insert_idea() code (or any caller, e.g.
-- lib/gsc_scout.py) has ever read/written; this follows the live code, not
-- the stale schema file.
--
-- brand_id is NOT a FK to brands(id) here, matching published_content /
-- engagements / schedule_tasks above (unlike fb_groups, whose brand_id is
-- NOT NULL and always brand-scoped) -- content_ideas rows may legitimately
-- predate brand scoping or come from a brand-agnostic run.
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS content_ideas (
    id                     TEXT        PRIMARY KEY,
    category               TEXT        NOT NULL,
    topic                  TEXT        NOT NULL,
    target_keyword         TEXT,
    nalla_context          TEXT,
    post_goal              TEXT,
    status                 TEXT        NOT NULL DEFAULT 'publish',
    input                  TEXT,
    brand_id               TEXT,
    brand_name             TEXT,
    wp_post_id             TEXT,
    wp_url                 TEXT,
    -- Reels crew (WP-post -> IG/FB Reels): reel_ig_video_path/reel_fb_video_path
    -- point at the SAME local file when reel_source='openart' (one shared clip,
    -- no per-platform overlay), distinct files when reel_source='fallback' (two
    -- platform-tuned slideshow renders). See lib/crew/reels/.
    reel_ig_video_path     TEXT,
    reel_fb_video_path     TEXT,
    reel_ig_caption        TEXT,
    reel_fb_caption        TEXT,
    reel_source            TEXT,
    reel_validation_flags  TEXT[],
    ig_reel_url            TEXT,
    fb_reel_url            TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_content_ideas_topic_brand
    ON content_ideas (lower(topic), COALESCE(brand_id, ''));
CREATE INDEX IF NOT EXISTS idx_content_ideas_status   ON content_ideas(status);
CREATE INDEX IF NOT EXISTS idx_content_ideas_brand_id ON content_ideas(brand_id);
