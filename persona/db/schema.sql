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
