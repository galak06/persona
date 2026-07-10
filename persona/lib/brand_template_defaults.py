"""Engine-sane default blocks for `brand_templates.py::render_config_json`.

These are NOT brand-specific onboarding answers -- they are the same
operational defaults every brand starts from (rate limits, approval gates,
dedup TTL, on-disk file layout, voice-validation block lists, the
relevance-scoring *formula shape*). Verbatim copies of `dogfoodandfun/
config.json`'s current values for the blocks the plan calls out as
"engine-sane defaults, not brand-specific onboarding questions" (see
`persona/lib/brand_templates.py`).

Kept in a separate module for the same reason `comment_generator_defaults.py`
does -- keeps the rendering module itself under the project's 300-line file
guideline and readable.

`recipe_card` is deliberately NOT copied verbatim here. dogfoodandfun's
`config.json` value for it is brand-specific (a WordPress media id that only
exists in dogfoodandfun's own WP install, a "Nalla Recipe Card" header
title) -- copying it into every new brand would silently point a fresh
brand's recipe-card stamping at someone else's asset. Stage 1 also excludes
the recipe pipeline entirely (see the plan's Stage 1 goal), so
`render_config_json` omits the key and lets `RecipeCardConfig`'s own
Pydantic defaults (genuinely generic: no stamp, generic title) apply.
"""

from __future__ import annotations

from typing import Any

# Verbatim from dogfoodandfun/config.json's `rate_limits` block.
RATE_LIMITS: dict[str, Any] = {
    "facebook": {
        "comments_per_day": 5,
        "group_visits_per_day": 6,
        "min_delay_between_comments_sec": 1,
        "max_delay_between_comments_sec": 5,
        "min_delay_between_group_visits_sec": 1,
        "max_delay_between_group_visits_sec": 10,
        "group_visit_schedule_hours": [9, 11, 14, 16, 18, 20],
    },
    "instagram": {
        "likes_per_day": 20,
        "comments_per_day": 10,
        "min_delay_between_likes_sec": 1,
        "max_delay_between_likes_sec": 10,
        "min_delay_between_comments_sec": 1,
        "max_delay_between_comments_sec": 10,
        "hashtag_rotation": {},
    },
}

# Verbatim from dogfoodandfun/config.json's `content_analysis` block, minus
# `keywords`/`competitor_accounts` (brand-driven -- see render_config_json).
# The relevance-scoring *formula shape* (thresholds + weight keys) stays
# fixed across brands this stage; only the keyword data it scores against is
# brand-specific (see the plan's "Known limitations" section).
CONTENT_ANALYSIS_DEFAULTS: dict[str, Any] = {
    "relevance_threshold": 0.70,
    "approval_threshold": 0.80,
    "site_cache_ttl_hours": 12,
    "site_cache_max_posts": 50,
    "site_crawl_depth": 2,
    "scoring_weights": {
        "food_nutrition_match": 0.40,
        "active_gps_match": 0.30,
        "question_format": 0.20,
        "reviewed_brand_mention": 0.20,
        "comment_count_5_to_50": 0.10,
        "post_under_24h": 0.10,
        "comment_count_over_100": -0.30,
        "competitor_account": -0.50,
    },
}

# Verbatim from dogfoodandfun/config.json's `approval_gates` block.
APPROVAL_GATES: dict[str, Any] = {
    "first_post_to_new_group": True,
    "comment_contains_url": True,
    "all_instagram_comments": True,
    "borderline_relevance_score": True,
    "borderline_score_range_lo": 0.70,
    "borderline_score_range_hi": 0.80,
}

# Verbatim from dogfoodandfun/config.json's `deduplication` block.
DEDUPLICATION: dict[str, Any] = {
    "ttl_days": 60,
    "cache_file": ".claude/state/dedup_cache.json",
}

# Verbatim from dogfoodandfun/config.json's `file_paths` block, apart from
# `facebook_tracker` (legacy Excel-tracker path -- fb_groups now lives in
# Postgres via `lib/groups_db`; kept only because `FilePathsConfig` requires
# a non-empty value).
FILE_PATHS: dict[str, Any] = {
    "state_dir": ".claude/state",
    "skills_dir": ".claude/skills",
    "logs_dir": "logs",
    "data_dir": "data",
    "lib_dir": "lib",
    "facebook_tracker": "../../facebook_groups_tracker.xlsx",
    "post_templates": "data/post_templates.json",
    "brand_voice_guide": "data/brand_voice_guide.md",
    "instagram_hashtags": "data/instagram_accounts.csv",
    "site_content_cache": "data/site_content_cache.json",
    "comment_queue": ".claude/state/comment_queue.json",
    "dedup_cache": ".claude/state/dedup_cache.json",
    "rate_limit_tracker": ".claude/state/rate_limit_tracker.json",
    "last_run": ".claude/state/last_run.json",
    "engagement_log": "logs/engagement_log.jsonl",
    "error_log": "logs/errors.log",
    "audit_trail": "logs/audit_trail.json",
}

# Verbatim from dogfoodandfun/config.json's `voice_validation` block.
VOICE_VALIDATION: dict[str, Any] = {
    "blocked_medical_terms": [
        "clinical",
        "veterinary-grade",
        "clinically proven",
        "studies show",
        "research indicates",
        "scientifically",
        "diagnosis",
        "symptoms",
        "treatment",
        "prescribe",
        "consult your vet before",
    ],
    "blocked_salesy_phrases": [
        "check out our",
        "visit our site",
        "click here",
        "buy now",
        "our product",
        "shop now",
        "affiliate",
        "promo code",
    ],
    "blocked_generic_openers": [
        "great post!",
        "love this!",
        "awesome!",
        "nice post",
        "amazing!",
    ],
    "min_comment_length": 40,
    "max_comment_length": 500,
    "must_end_with_question": True,
}

# Verbatim from dogfoodandfun/config.json's `social_channels.facebook` block,
# minus `page_url` (brand-supplied). `tracker_file`/`tracker_sheet` are the
# same legacy-but-required Excel fields as FILE_PATHS.facebook_tracker above.
FACEBOOK_CHANNEL_DEFAULTS: dict[str, Any] = {
    "tracker_file": "../../facebook_groups_tracker.xlsx",
    "tracker_sheet": "Groups Database",
}

# Verbatim from dogfoodandfun/config.json's `social_channels.instagram`
# block, minus `profile_url` (brand-supplied).
INSTAGRAM_CHANNEL_DEFAULTS: dict[str, Any] = {
    "hashtags_file": "data/instagram_accounts.csv",
}

# Disabled-by-default channel note, matching dogfoodandfun/config.json's
# twitter/tiktok blocks verbatim.
DISABLED_CHANNEL_NOTE = "Set enabled: true and add profile_url to activate"
