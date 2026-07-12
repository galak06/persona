-- Persona — local Postgres schema.
--
-- Self-initializing schema for the self-hosted stack. Mounted at
-- /docker-entrypoint-initdb.d/schema.sql so a fresh `postgres:16` container
-- applies it automatically on first `docker compose up` — no manual dashboard
-- step, no Supabase-specific extensions/RLS policies. All statements are
-- `IF NOT EXISTS`, safe to re-run.
--
-- Scope: only the tables consumed by the modules migrating off Supabase this
-- stage — groups_db, engagements_db, worker_db, schedule_db. `recipes_db`
-- (recipes, raw_scrapes), `content_ideas`, and `oauth_tokens` stay on whatever
-- they use today and are intentionally NOT included here.
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
ALTER TABLE brands ADD COLUMN IF NOT EXISTS enabled_flows       JSONB DEFAULT '["ig-scanner","fb-scanner"]';
ALTER TABLE brands ADD COLUMN IF NOT EXISTS status              TEXT  NOT NULL DEFAULT 'draft';
ALTER TABLE brands ADD COLUMN IF NOT EXISTS brand_dir           TEXT  DEFAULT '';
ALTER TABLE brands ADD COLUMN IF NOT EXISTS extra               JSONB DEFAULT '{}';
-- Additive (PR5 — brand settings): whether this brand's Playwright scanners
-- run with a visible browser window. Existing rows default to TRUE
-- (production-safe, matches `lib.local_env.get_runtime_headless()`'s own
-- fallback), so this migration is a no-op behavior-wise for brands that
-- never customize it.
ALTER TABLE brands ADD COLUMN IF NOT EXISTS headless            BOOLEAN NOT NULL DEFAULT TRUE;
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
