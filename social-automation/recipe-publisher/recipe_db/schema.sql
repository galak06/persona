-- Recipe DB schema. Idempotent: safe to run on every migrate().

-- Immutable raw scrape payloads (JSON-LD / HTML), keyed by content hash.
CREATE TABLE IF NOT EXISTS raw_scrapes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url   TEXT NOT NULL,
    source_name  TEXT,
    scraped_at   TEXT NOT NULL,
    content_hash TEXT NOT NULL UNIQUE,
    payload      TEXT NOT NULL
);

-- Normalized recipes. id is the title slug (see models.slugify).
CREATE TABLE IF NOT EXISTS recipes (
    id             TEXT PRIMARY KEY,
    title          TEXT NOT NULL,
    name           TEXT,
    display_name   TEXT DEFAULT '',
    artifacts_path TEXT DEFAULT '',
    card_path      TEXT DEFAULT '',
    card_created_at TEXT DEFAULT '',
    wp_url         TEXT DEFAULT '',
    ig_url         TEXT DEFAULT '',
    fb_url         TEXT DEFAULT '',
    category       TEXT,
    prep_minutes   INTEGER,
    cook_minutes   INTEGER,
    total_minutes  INTEGER,
    servings       TEXT,
    ingredients    TEXT,
    steps          TEXT,
    nutrition      TEXT,
    tags           TEXT,
    hero_image_url TEXT,
    source_url     TEXT,
    source_name    TEXT,
    license        TEXT,
    content_hash   TEXT UNIQUE,
    status         TEXT NOT NULL DEFAULT 'scraped',
    toxic_flags    TEXT,
    dog_safe       INTEGER DEFAULT 0,
    override       INTEGER DEFAULT 0,
    publish_status TEXT DEFAULT '{}',
    season_tags    TEXT DEFAULT '[]',
    affiliate_products TEXT DEFAULT '[]',
    generated_content TEXT DEFAULT '{}',
    content_status TEXT NOT NULL DEFAULT 'none',
    publish_results TEXT DEFAULT '[]',
    -- Decoupled-worker artifact markers (see recipe-publisher/workers/).
    wp_post_id     INTEGER DEFAULT NULL,
    pdf_url        TEXT DEFAULT '',
    slides_created_at TEXT DEFAULT '',
    slides_count   INTEGER DEFAULT 0,
    reel_created_at TEXT DEFAULT '',
    audio_ready_at TEXT DEFAULT '',
    social_published_at TEXT DEFAULT '',
    created_at     TEXT,
    updated_at     TEXT
);

-- Full-text search over title/ingredients/tags, shadowing the recipes table.
CREATE VIRTUAL TABLE IF NOT EXISTS recipes_fts USING fts5(
    title,
    ingredients,
    tags,
    content='recipes',
    content_rowid='rowid'
);

-- Keep the FTS index in sync with the recipes table.
CREATE TRIGGER IF NOT EXISTS recipes_ai AFTER INSERT ON recipes BEGIN
    INSERT INTO recipes_fts (rowid, title, ingredients, tags)
    VALUES (new.rowid, new.title, new.ingredients, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS recipes_ad AFTER DELETE ON recipes BEGIN
    INSERT INTO recipes_fts (recipes_fts, rowid, title, ingredients, tags)
    VALUES ('delete', old.rowid, old.title, old.ingredients, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS recipes_au AFTER UPDATE ON recipes BEGIN
    INSERT INTO recipes_fts (recipes_fts, rowid, title, ingredients, tags)
    VALUES ('delete', old.rowid, old.title, old.ingredients, old.tags);
    INSERT INTO recipes_fts (rowid, title, ingredients, tags)
    VALUES (new.rowid, new.title, new.ingredients, new.tags);
END;
