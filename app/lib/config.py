import json
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from lib.brand_context import BrandContext, BrandPaths, default_brand_dir

# Re-exported: callers already import these two from `lib.config`
# (`api/schedule_config.py`, `api/approval_api.py`, `lib/local_env.py`, ...).
__all__ = ["AppSettings", "BrandPaths", "default_brand_dir", "load_config", "settings"]


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
    own_account: str = ""


class TwitterConfig(BaseModel):
    enabled: bool
    profile_url: str
    note: str | None = None


class TiktokConfig(BaseModel):
    enabled: bool
    profile_url: str
    note: str | None = None


class SocialChannelsConfig(BaseModel):
    facebook: FacebookConfig
    instagram: InstagramConfig
    twitter: TwitterConfig
    tiktok: TiktokConfig


class ContentAnalysisConfig(BaseModel):
    relevance_threshold: float
    approval_threshold: float
    site_cache_ttl_hours: int
    site_cache_max_posts: int
    site_crawl_depth: int
    keywords: dict[str, list[str]]
    scoring_weights: dict[str, float]
    competitor_accounts: list[str] = []


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
    stamp_media_id: int = 0  # 0 = no stamp
    footer_text: str = ""
    font_regular_path: str = ""  # relative to project root
    font_bold_path: str = ""
    black_and_white: bool = False


class AppSettings(BaseModel):
    site: SiteConfig
    social_channels: SocialChannelsConfig
    # No `rate_limits`: quotas live in `profiles/<platform>.json` and reach
    # runtime through the generated `data/rate_limits.json` (ADR 0004). The
    # typed tree that used to sit here had zero readers while every engager
    # re-read the same numbers from raw JSON -- a second copy that drifted.
    # A `rate_limits` block left in a brand config.json is simply ignored.
    content_analysis: ContentAnalysisConfig
    approval_gates: ApprovalGatesConfig
    deduplication: DeduplicationConfig
    file_paths: FilePathsConfig
    voice_validation: VoiceValidationConfig
    recipe_card: RecipeCardConfig = RecipeCardConfig()
    paths: BrandPaths | None = None


def load_config(ctx: BrandContext | None = None) -> AppSettings:
    """Parse `<brand_dir>/config.json` into `AppSettings`.

    `ctx=None` means "this process's brand" and keeps the original failure mode:
    a clear error when `BRAND_DIR` is unset. The env/secrets merge that used to
    fire at module import now happens here, behind the context — deferred to the
    moment a caller actually needs configuration.
    """
    ctx = ctx or BrandContext.from_env()
    try:
        ctx.load_env(apply_secrets=False)
    except ImportError:  # pragma: no cover - lib.local_env is always present
        pass

    config_file = ctx.brand_dir / "config.json"
    if not config_file.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_file}")

    with config_file.open("r", encoding="utf-8") as f:
        data = json.load(f)

    parsed = AppSettings(**data)
    parsed.paths = ctx.paths
    return parsed


_cached: AppSettings | None = None


def get_settings() -> AppSettings:
    """The process's `AppSettings`, loaded once on first use."""
    global _cached
    if _cached is None:
        _cached = load_config()
    return _cached


def reset_settings_cache() -> None:
    """Drop the cached settings. For tests that change `BRAND_DIR` mid-run."""
    global _cached
    _cached = None


class _LazySettings:
    """Defers `load_config()` to the first attribute touch.

    Why an object and not PEP 562's module `__getattr__`: `from lib.config
    import settings` appears at 31 sites and `import lib.config` at none, and a
    module-level `__getattr__` fires on `from X import name` at the *importing*
    module's import time. Several of those imports are module top-level
    (`lib/bootstrap.py`, `lib/rate_limiter.py`, `lib/groups_queue.py`), so a
    module hook would leave resolution exactly as eager as it was. Binding a
    forwarding object is cheap; resolution waits for `settings.<anything>`.
    """

    def __getattr__(self, name: str) -> Any:
        return getattr(get_settings(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        # Tests monkeypatch through this; forward so they mutate the real model.
        setattr(get_settings(), name, value)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<lazy {get_settings()!r}>"


if TYPE_CHECKING:
    # Type checkers see the real model, so all 31 call sites keep full attribute
    # checking. The runtime object forwards every access to exactly that model.
    settings: AppSettings
else:
    settings = _LazySettings()
