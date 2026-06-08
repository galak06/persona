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
    # Flat published URLs, denormalized from publish_status for direct queries.
    wp_url: str = ""
    ig_url: str = ""
    fb_url: str = ""
    status: str = RecipeStatus.SCRAPED
    toxic_flags: list[str] = field(default_factory=list)
    dog_safe: bool = False
    override: bool = False
    # Per-channel publish status: {channel: {state, url, ref, at}} where
    # channel is one of wp / pdf / ig / fb. Synced from publish records.
    publish_status: dict[str, dict[str, str]] = field(default_factory=dict)

    def ensure_id(self) -> str:
        """Populate `id` from the recipe name slug if not already set."""
        if not self.id:
            self.id = slugify(self.name)
        return self.id
