# pyright: reportMissingImports=false
"""Pydantic models for the read-only recipe-DB browse API.

Mirrors the `recipe_db` SQLite rows (scraped recipes + ratings + dog-safety
verdict) for the web UI. Kept in its own module so `schemas.py` stays small.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RecipeIngredient(BaseModel):
    """A single ingredient line from a scraped recipe."""

    item: str
    qty: str = ""
    unit: str = ""
    notes: str = ""


class PublishChannel(BaseModel):
    """Publish state for one channel (wp / pdf / ig / fb).

    IG-only extras (``caption`` / ``reel_url`` / ``post_url``) carry the
    Instagram single-image post + reel split surfaced in the UI popup. They
    stay empty for the other channels.
    """

    state: str = ""  # "published" | "" (not published)
    url: str = ""
    ref: str = ""
    at: str = ""
    caption: str = ""  # IG: drafted caption text
    reel_url: str = ""  # IG: reel permalink
    post_url: str = ""  # IG: single-image post permalink (empty until posted)


class AffiliateProduct(BaseModel):
    """One matched Amazon-Associates product (from the affiliate-matching phase)."""

    key: str
    asin: str
    display: str


class SyncResponse(BaseModel):
    """Result of POST /api/v1/recipes/sync-publish."""

    updated: int
    total: int


class RecipeSummary(BaseModel):
    """List-row view of a stored recipe (no ingredients/steps)."""

    id: str
    name: str
    display_name: str = ""
    artifacts_path: str = ""  # absolute local path to the artifact folder
    card_path: str = ""  # absolute local path to the rendered recipe card, or ""
    card_created_at: str = ""  # when the card was generated (ISO), or ""
    card_html_path: str = ""  # absolute local path to the self-contained card HTML, or ""
    card_html_created_at: str = ""  # when the card HTML was written (ISO), or ""
    wp_url: str = ""
    ig_url: str = ""
    fb_url: str = ""
    published_at: str = ""  # best publish date across channels (ISO), or ""
    category: str = ""
    dog_safe: bool = False
    toxic_flags: list[str] = Field(default_factory=list)
    season_tags: list[str] = Field(default_factory=list)  # [] = all-season
    affiliate_products: list[AffiliateProduct] = Field(default_factory=list)
    content_status: str = "none"  # publish-content lifecycle (models.ContentStatus)
    status: str
    source_url: str = ""
    source_name: str = ""
    prep_minutes: int = 0
    cook_minutes: int = 0
    total_minutes: int = 0
    servings: str = ""
    publish_status: dict[str, PublishChannel] = Field(default_factory=dict)


class RecipeMedia(BaseModel):
    """On-disk media for a recipe, as BRAND_DIR-relative paths.

    Sourced from the row's ``generated_content.media`` manifest (links only —
    the bytes stay on disk). The UI turns each path into a serving URL via the
    ``/recipes/{id}/media-file`` endpoint.
    """

    images: list[str] = Field(default_factory=list)
    reels: list[str] = Field(default_factory=list)  # video files
    audio: list[str] = Field(default_factory=list)
    featured_image: str | None = None  # best image path, or None


class RecipeDetail(RecipeSummary):
    """Full recipe payload for ``GET /api/v1/recipes/{id}``."""

    ingredients: list[RecipeIngredient] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    nutrition: dict[str, str] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    hero_image_url: str = ""
    media: RecipeMedia | None = None  # reels/photos/audio links, when present


class RecipesResponse(BaseModel):
    """Envelope for ``GET /api/v1/recipes``."""

    recipes: list[RecipeSummary]
    total: int


class StatusChangeResponse(BaseModel):
    """Result of an approval/rejection transition."""

    id: str
    content_status: str


class AnalyticsResponse(BaseModel):
    """Aggregated publish outcomes (phase 10, local outcome log)."""

    recipes: int
    attempts: int
    by_platform: dict[str, dict[str, int]] = Field(default_factory=dict)
    by_status: dict[str, int] = Field(default_factory=dict)


class ArtifactItem(BaseModel):
    """One file in a recipe's artifact folder, listed for the UI."""

    name: str  # file name, e.g. "recipe_card.png"
    path: str  # path relative to the recipe's artifact folder, e.g. "images/recipe_card.png"
    kind: str  # "image" | "pdf" | "json" | "other"
    size: int = 0  # bytes


class ArtifactsResponse(BaseModel):
    """Envelope for ``GET /api/v1/recipes/{id}/artifacts``."""

    recipe_id: str
    artifacts: list[ArtifactItem] = Field(default_factory=list)
    total: int = 0
