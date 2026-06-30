import json
import os
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel


class SiteConfig(BaseModel):
    name: str
    url: str
    rss_feed: str
    sitemap: str
    brand_persona: str
    mascot_name: str
    niche: str
    target_audience: str


class FacebookConfig(BaseModel):
    enabled: bool
    page_url: str
    page_name: str
    tracker_file: str
    tracker_sheet: str


class InstagramConfig(BaseModel):
    enabled: bool
    profile_url: str
    hashtags_file: str


class TwitterConfig(BaseModel):
    enabled: bool
    profile_url: str
    note: Optional[str] = None


class TiktokConfig(BaseModel):
    enabled: bool
    profile_url: str
    note: Optional[str] = None


class SocialChannelsConfig(BaseModel):
    facebook: FacebookConfig
    instagram: InstagramConfig
    twitter: TwitterConfig
    tiktok: TiktokConfig


class FacebookRateLimits(BaseModel):
    comments_per_day: int
    group_visits_per_day: int
    min_delay_between_comments_sec: int
    max_delay_between_comments_sec: int
    min_delay_between_group_visits_sec: int
    max_delay_between_group_visits_sec: int
    group_visit_schedule_hours: list[int]
    _note_group_visits: Optional[str] = None


class InstagramRateLimits(BaseModel):
    likes_per_day: int
    comments_per_day: int
    min_delay_between_likes_sec: int
    max_delay_between_likes_sec: int
    min_delay_between_comments_sec: int
    max_delay_between_comments_sec: int
    _note_likes: Optional[str] = None
    hashtag_rotation: dict[str, Any]


class RateLimitsConfig(BaseModel):
    facebook: FacebookRateLimits
    instagram: InstagramRateLimits


class ContentAnalysisConfig(BaseModel):
    relevance_threshold: float
    approval_threshold: float
    site_cache_ttl_hours: int
    site_cache_max_posts: int
    site_crawl_depth: int
    keywords: dict[str, list[str]]
    scoring_weights: dict[str, float]


class ApprovalGatesConfig(BaseModel):
    first_post_to_new_group: bool
    comment_contains_url: bool
    all_instagram_comments: bool
    borderline_relevance_score: bool
    borderline_score_range_lo: float
    borderline_score_range_hi: float


class DeduplicationConfig(BaseModel):
    ttl_days: int
    cache_file: str


class FilePathsConfig(BaseModel):
    state_dir: str
    skills_dir: str
    logs_dir: str
    data_dir: str
    lib_dir: str
    facebook_tracker: str
    post_templates: str
    brand_voice_guide: str
    instagram_hashtags: str
    site_content_cache: str
    comment_queue: str
    dedup_cache: str
    rate_limit_tracker: str
    last_run: str
    engagement_log: str
    error_log: str
    audit_trail: str


class VoiceValidationConfig(BaseModel):
    blocked_medical_terms: list[str]
    blocked_salesy_phrases: list[str]
    blocked_generic_openers: list[str]
    min_comment_length: int
    max_comment_length: int
    must_end_with_question: bool


class RecipeCardConfig(BaseModel):
    enabled: bool = True
    header_title: str = "Recipe Card"
    stamp_media_id: int = 0       # 0 = no stamp
    footer_text: str = ""
    font_regular_path: str = ""   # relative to project root
    font_bold_path: str = ""
    black_and_white: bool = False


class BrandPaths(BaseModel):
    brand_dir: Path
    data_dir: Path
    state_dir: Path
    logs_dir: Path
    backups_dir: Path
    campaigns_dir: Path
    schedule_file: Path

    # Specific file paths within brand
    brand_voice_guide: Path
    campaigns: Path
    citation_sources: Path
    competitors: Path
    content_rules: Path
    groups_tracker: Path
    instagram_accounts: Path
    keyword_clusters: Path
    post_templates: Path
    recipe_products: Path

    # State paths
    comment_queue: Path  # legacy shared queue — retained for migration/back-compat
    instagram_comment_queue: Path  # IG loop owns its own queue
    facebook_comment_queue: Path  # FB loop owns its own queue
    ideator_queue: Path
    campaign_verify_queue: Path
    dedup_cache: Path
    rate_limit_tracker: Path
    last_run: Path
    facebook_session: Path
    instagram_session: Path
    tiktok_session: Path
    pending_groups: Path


class AppSettings(BaseModel):
    site: SiteConfig
    social_channels: SocialChannelsConfig
    rate_limits: RateLimitsConfig
    content_analysis: ContentAnalysisConfig
    approval_gates: ApprovalGatesConfig
    deduplication: DeduplicationConfig
    file_paths: FilePathsConfig
    voice_validation: VoiceValidationConfig
    recipe_card: RecipeCardConfig = RecipeCardConfig()
    paths: Optional[BrandPaths] = None


def _resolve_paths(brand_dir: Path) -> BrandPaths:
    data_dir = brand_dir / "data"
    state_dir = brand_dir / "state"
    logs_dir = brand_dir / "logs"

    return BrandPaths(
        brand_dir=brand_dir,
        data_dir=data_dir,
        state_dir=state_dir,
        logs_dir=logs_dir,
        backups_dir=brand_dir / "backups",
        campaigns_dir=brand_dir / "campaigns",
        schedule_file=brand_dir / "schedule.json",
        brand_voice_guide=data_dir / "config" / "brand_voice_guide.md",
        campaigns=data_dir / "config" / "campaigns.json",
        citation_sources=data_dir / "config" / "citation_sources.json",
        competitors=data_dir / "config" / "competitors.json",
        content_rules=data_dir / "config" / "content_rules.json",
        groups_tracker=data_dir / "trackers" / "groups_tracker.json",
        instagram_accounts=data_dir / "config" / "instagram_accounts.csv",
        keyword_clusters=data_dir / "config" / "keyword_clusters.json",
        post_templates=data_dir / "config" / "post_templates.json",
        recipe_products=data_dir / "config" / "recipe_products.json",
        comment_queue=state_dir / "comment_queue.json",
        instagram_comment_queue=state_dir / "instagram_comment_queue.json",
        facebook_comment_queue=state_dir / "facebook_comment_queue.json",
        ideator_queue=state_dir / "ideator_queue.json",
        campaign_verify_queue=state_dir / "campaign_verify_queue.json",
        dedup_cache=state_dir / "dedup_cache.json",
        rate_limit_tracker=state_dir / "rate_limit_tracker.json",
        last_run=state_dir / "last_run.json",
        facebook_session=state_dir / "facebook_session.json",
        instagram_session=state_dir / "instagram_session.json",
        tiktok_session=state_dir / "tiktok_session.json",
        pending_groups=state_dir / "pending_groups.json",
    )


def load_config() -> AppSettings:
    brand_dir_str = os.environ.get("BRAND_DIR")
    if not brand_dir_str:
        raise ValueError(
            "BRAND_DIR environment variable is not set. "
            "Please set it to the path of the brand configuration directory."
        )

    brand_dir = Path(brand_dir_str).resolve()
    config_file = brand_dir / "config.json"

    if not config_file.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_file}")

    with config_file.open("r", encoding="utf-8") as f:
        data = json.load(f)

    settings = AppSettings(**data)
    settings.paths = _resolve_paths(brand_dir)
    return settings


# Load local environment variables from .claude/settings.local.json
try:
    from lib.local_env import load_local_env
    load_local_env()
except ImportError:
    pass

# Singleton instance
settings = load_config()
