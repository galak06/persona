"""Shared data contracts for the recipe DB layer.

These dataclasses are the canonical contract imported by every other module in
the recipe pipeline. Field names here are authoritative — do not rename without
updating downstream consumers (normalizer, safety checker, seed exporter).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


class RecipeStatus:
    """Allowed values for `RecipeRow.status` (pipeline stages, in order)."""

    SCRAPED: str = "scraped"
    NORMALIZED: str = "normalized"
    SAFETY_CHECKED: str = "safety_checked"
    SEED_EXPORTED: str = "seed_exported"

    ALL: frozenset[str] = frozenset(
        {"scraped", "normalized", "safety_checked", "seed_exported"}
    )


class ContentStatus:
    """Allowed values for `RecipeRow.content_status` (publish-content lifecycle).

    Distinct from `RecipeStatus` (the scrape pipeline). Drives the
    generation → review → approval → publish phases:
        none -> generated -> pending -> approved -> published
                                     \\-> rejected
    """

    NONE: str = "none"
    GENERATED: str = "generated"
    PENDING: str = "pending"
    APPROVED: str = "approved"
    REJECTED: str = "rejected"
    PUBLISHED: str = "published"

    ALL: frozenset[str] = frozenset(
        {"none", "generated", "pending", "approved", "rejected", "published"}
    )


def slugify(name: str) -> str:
    """Lowercase, hyphenate, strip non-alphanumerics.

    Used both for recipe ids and as the normalized-title dedup key. Collapses
    runs of non-alphanumeric characters to single hyphens and trims leading /
    trailing hyphens.
    """
    lowered = name.strip().lower()
    hyphenated = re.sub(r"[^a-z0-9]+", "-", lowered)
    return hyphenated.strip("-")


@dataclass
class Ingredient:
    """A single recipe ingredient line.

    `qty` is intentionally a string (e.g. "1 1/2") to preserve fractions and
    ranges; any of `qty`, `unit`, `notes` may be empty strings.
    """

    item: str
    qty: str = ""
    unit: str = ""
    notes: str = ""


@dataclass
class ScrapedRecipe:
    """A recipe parsed from a source page, before normalization / safety."""

    name: str
    ingredients: list[Ingredient] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    prep_minutes: int = 0
    cook_minutes: int = 0
    total_minutes: int = 0
    servings: str = ""
    nutrition: dict[str, str] = field(default_factory=dict)
    category: str = ""
    tags: list[str] = field(default_factory=list)
    hero_image_url: str = ""
    source_url: str = ""
    source_name: str = ""
    license: str = ""
    content_hash: str = ""


@dataclass
class RecipeRow:
    """A persisted recipe row: all `ScrapedRecipe` fields plus DB metadata."""

    # ScrapedRecipe fields
    name: str
    ingredients: list[Ingredient] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    prep_minutes: int = 0
    cook_minutes: int = 0
    total_minutes: int = 0
    servings: str = ""
    nutrition: dict[str, str] = field(default_factory=dict)
    category: str = ""
    tags: list[str] = field(default_factory=list)
    hero_image_url: str = ""
    source_url: str = ""
    source_name: str = ""
    license: str = ""
    content_hash: str = ""
    # DB metadata
    id: str = ""
    # Original, brand-voice name shown in place of the scraped source title.
    # Empty until generated; consumers fall back to ``name`` when blank.
    display_name: str = ""
    # Local artifact folder (images/reels/audio/meta), relative to BRAND_DIR.
    # Empty until the recipe has generated/imported assets on disk.
    artifacts_path: str = ""
    # ISO-8601 timestamp set when the HTML page export phase completes.
    # Empty until the HTML export phase has run for this recipe.
    html_exported_at: str | None = None
    # Rendered static recipe-card image (BRAND_DIR-relative) + when it was
    # generated. Empty until the card template has been created for this recipe.
    card_path: str = ""
    card_created_at: str = ""
    # Flat published URLs, denormalized from publish_status for direct queries.
    wp_url: str = ""
    ig_url: str = ""
    fb_url: str = ""
    status: str = RecipeStatus.SCRAPED
    toxic_flags: list[str] = field(default_factory=list)
    dog_safe: bool = False
    override: bool = False
    # Seasons this recipe suits (subset of pipeline.seasons.SEASONS). Empty =
    # all-season (eligible year-round). Populated by the seasonal-selection
    # phase; see pipeline/seasonal_selection.py.
    season_tags: list[str] = field(default_factory=list)
    # Matched affiliate products: list of {key, asin, display}. Populated by the
    # affiliate-matching phase; see pipeline/affiliate_matching.py.
    affiliate_products: list[dict[str, str]] = field(default_factory=list)
    # Generated draft content: {title, body_markdown, ig_caption, image_brief,
    # generated_at}. Written by the content-generation phase (pipeline/
    # content_generation.py). Empty until generated.
    generated_content: dict[str, str] = field(default_factory=dict)
    # Publish-content lifecycle state (see ContentStatus). Advances through the
    # generation/review/approval/publish phases.
    content_status: str = ContentStatus.NONE
    # Per-attempt publish outcomes: list of {platform, status, ref, url, at,
    # attempts, error}. Written by the publishing/retry phases; read by the
    # analytics phase. Doubles as the local outcome log.
    publish_results: list[dict[str, str]] = field(default_factory=list)
    # Per-channel publish status: {channel: {state, url, ref, at}} where
    # channel is one of wp / pdf / ig / fb. Synced from publish records.
    publish_status: dict[str, dict[str, str]] = field(default_factory=dict)
    # --- Decoupled-worker artifact markers (recipe-publisher/workers/). Each is
    # written by exactly one worker; a worker's poll predicate is
    # "(prerequisite filled) AND (my output empty)". Worker-written-only:
    # never set on insert/upsert, so DB defaults apply on scrape.
    wp_post_id: int | None = None          # Worker A: numeric WP draft/post id
    pdf_url: str = ""                      # Worker A: uploaded recipe-card PDF url
    image_created_at: str = ""             # Worker E: ISO ts when hero image saved
    card_html_path: str = ""               # Worker HTML: BRAND_DIR-relative path to card HTML
    card_html_created_at: str = ""         # Worker HTML: ISO ts when card HTML written
    slides_created_at: str = ""            # Worker B: ISO ts when slides saved
    slides_count: int = 0                  # Worker B: number of slides saved
    reel_created_at: str = ""              # Worker C: ISO ts when source.mp4 made
    audio_ready_at: str = ""               # Worker D: ISO ts when audio detected
    social_published_at: str = ""          # Worker D: ISO ts when IG/FB/Pin done

    def ensure_id(self) -> str:
        """Populate `id` from the recipe name slug if not already set."""
        if not self.id:
            self.id = slugify(self.name)
        return self.id
