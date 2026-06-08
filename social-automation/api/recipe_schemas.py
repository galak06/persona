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
    wp_url: str = ""
    ig_url: str = ""
    fb_url: str = ""
    published_at: str = ""  # best publish date across channels (ISO), or ""
    category: str = ""
    dog_safe: bool = False
    toxic_flags: list[str] = Field(default_factory=list)
    status: str
    source_url: str = ""
    source_name: str = ""
    prep_minutes: int = 0
    cook_minutes: int = 0
    total_minutes: int = 0
    servings: str = ""
    publish_status: dict[str, PublishChannel] = Field(default_factory=dict)


class RecipeDetail(RecipeSummary):
    """Full recipe payload for ``GET /api/v1/recipes/{id}``."""

    ingredients: list[RecipeIngredient] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    nutrition: dict[str, str] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    hero_image_url: str = ""


class RecipesResponse(BaseModel):
    """Envelope for ``GET /api/v1/recipes``."""

    recipes: list[RecipeSummary]
    total: int
