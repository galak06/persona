-- Run this in Supabase Dashboard → SQL Editor before running migrate_sqlite_to_supabase.py
-- All tables use IF NOT EXISTS — safe to re-run.

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

-- ────────────────────────────────────────────────────────────────────────────
-- raw_scrapes + recipes (from recipes.db)
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS raw_scrapes (
    id              BIGSERIAL   PRIMARY KEY,
    source_url      TEXT        NOT NULL,
    source_name     TEXT,
    scraped_at      TEXT        NOT NULL,
    content_hash    TEXT        NOT NULL UNIQUE,
    payload         JSONB       NOT NULL
);

CREATE TABLE IF NOT EXISTS recipes (
    id                  TEXT        PRIMARY KEY,
    title               TEXT        NOT NULL,
    name                TEXT,
    category            TEXT,
    prep_minutes        INTEGER,
    cook_minutes        INTEGER,
    total_minutes       INTEGER,
    servings            TEXT,
    ingredients         JSONB,
    steps               JSONB,
    nutrition           JSONB,
    tags                JSONB,
    hero_image_url      TEXT,
    source_url          TEXT,
    source_name         TEXT,
    license             TEXT,
    content_hash        TEXT        UNIQUE,
    status              TEXT        NOT NULL    DEFAULT 'scraped',
    toxic_flags         JSONB,
    dog_safe            INTEGER                 DEFAULT 0,
    override            INTEGER                 DEFAULT 0,
    created_at          TEXT,
    updated_at          TEXT,
    publish_status      JSONB                   DEFAULT '{}',
    display_name        TEXT                    DEFAULT '',
    artifacts_path      TEXT                    DEFAULT '',
    wp_url              TEXT                    DEFAULT '',
    ig_url              TEXT                    DEFAULT '',
    fb_url              TEXT                    DEFAULT '',
    card_path           TEXT                    DEFAULT '',
    card_created_at     TEXT                    DEFAULT '',
    season_tags         JSONB                   DEFAULT '[]',
    affiliate_products  JSONB                   DEFAULT '[]',
    generated_content   JSONB                   DEFAULT '{}',
    content_status      TEXT        NOT NULL    DEFAULT 'none',
    publish_results     JSONB                   DEFAULT '[]',
    html_exported_at    TEXT,
    wp_post_id          INTEGER,
    pdf_url             TEXT                    DEFAULT '',
    slides_created_at   TEXT                    DEFAULT '',
    slides_count        INTEGER                 DEFAULT 0,
    reel_created_at     TEXT                    DEFAULT '',
    audio_ready_at      TEXT                    DEFAULT '',
    social_published_at TEXT                    DEFAULT '',
    image_created_at    TEXT                    DEFAULT '',
    card_html_path      TEXT                    DEFAULT '',
    card_html_created_at TEXT                   DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_recipes_status         ON recipes(status);
CREATE INDEX IF NOT EXISTS idx_recipes_dog_safe        ON recipes(dog_safe);
CREATE INDEX IF NOT EXISTS idx_recipes_content_status  ON recipes(content_status);
CREATE INDEX IF NOT EXISTS idx_recipes_fts ON recipes USING gin(
    to_tsvector('english',
        coalesce(title, '') || ' ' || coalesce(ingredients::text, '') || ' ' || coalesce(tags::text, '')
    )
);

-- ────────────────────────────────────────────────────────────────────────────
-- completed_tasks (existing dedup table used by lib/dedup_pg.py)
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
