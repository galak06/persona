-- Engagements DB schema. Idempotent: safe to run on every migrate().
-- One row per PUBLISHED post or comment across platforms — the queryable
-- history behind the JSONL engagement log. Lives at
-- ${BRAND_DIR}/data/db/engagements.db (separate from groups.db / recipes.db).

CREATE TABLE IF NOT EXISTS engagements (
    id            TEXT PRIMARY KEY,    -- dedup key: slug of {platform}:{kind}:{ref}
    brand_id      TEXT NOT NULL DEFAULT '',
    platform      TEXT NOT NULL,       -- facebook | instagram | wordpress
    kind          TEXT NOT NULL,       -- comment | link_post | feed_post | reel | page_post
    status        TEXT NOT NULL DEFAULT 'posted',  -- posted | failed
    target_name   TEXT DEFAULT '',     -- group name / hashtag / page name
    target_url    TEXT DEFAULT '',     -- third-party post URL (comment) or destination URL
    permalink     TEXT DEFAULT '',     -- OUR published item's URL/permalink, when known
    content       TEXT DEFAULT '',     -- comment text / post caption (may be truncated)
    source_ref    TEXT DEFAULT '',     -- recipe slug / wp_post_id / source post id
    error         TEXT DEFAULT '',     -- failure reason when status='failed'
    posted_at     TEXT DEFAULT '',     -- when it was published (ISO)
    created_at    TEXT,
    updated_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_engagements_platform_kind ON engagements(platform, kind);
CREATE INDEX IF NOT EXISTS idx_engagements_status ON engagements(status);
CREATE INDEX IF NOT EXISTS idx_engagements_posted_at ON engagements(posted_at);
