-- Facebook groups DB schema. Idempotent: safe to run on every migrate().
-- Models a brand and its FB groups (each group with details + status),
-- migrated out of data/trackers/groups_tracker.json.

-- One row per brand (single-brand system today; FK target for fb_groups).
CREATE TABLE IF NOT EXISTS brands (
    id          TEXT PRIMARY KEY,   -- stable slug, e.g. "dogfoodandfun"
    name        TEXT NOT NULL,
    persona     TEXT DEFAULT '',
    site_url    TEXT DEFAULT '',
    created_at  TEXT,
    updated_at  TEXT
);

-- One row per Facebook group, owned by a brand.
CREATE TABLE IF NOT EXISTS fb_groups (
    id                       TEXT PRIMARY KEY,   -- fb group id / slug from group_url
    brand_id                 TEXT NOT NULL REFERENCES brands(id),
    group_url                TEXT NOT NULL UNIQUE,
    group_name               TEXT DEFAULT '',
    status                   TEXT NOT NULL DEFAULT 'join_requested',
    joined_at                TEXT DEFAULT '',
    rules                    TEXT DEFAULT '',
    source_notification      TEXT DEFAULT '',
    privacy                  TEXT DEFAULT '',
    member_count             TEXT DEFAULT '',
    posting_mode             TEXT DEFAULT '',
    self_promo_allowed       TEXT DEFAULT '',
    category                 TEXT DEFAULT '',
    notes                    TEXT DEFAULT '[]',  -- JSON array of {at, text}
    last_post_status         TEXT DEFAULT '',
    last_post_caption        TEXT DEFAULT '',
    last_post_permalink      TEXT DEFAULT '',
    last_post_at             TEXT DEFAULT '',
    last_reel_caption        TEXT DEFAULT '',
    last_reel_post_at        TEXT DEFAULT '',
    last_reel_post_permalink TEXT DEFAULT '',
    last_checked_at          TEXT DEFAULT '',
    extra                    TEXT DEFAULT '{}',  -- any keys not modeled above
    created_at               TEXT,
    updated_at               TEXT
);

CREATE INDEX IF NOT EXISTS idx_fb_groups_status ON fb_groups(status);
CREATE INDEX IF NOT EXISTS idx_fb_groups_brand ON fb_groups(brand_id);
