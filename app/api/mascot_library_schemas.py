"""Pydantic models for the mascot reference-image library API.

Mirrors the `library.json` manifest entries (`lib.crew.mascot_library`) for
the web UI. Kept in its own module so `mascot_library_api.py` stays small --
that file is then transport only: the router, its seven routes, and the
request-handling helpers.

Nothing here imports FastAPI. The two module-level helpers (`to_model`,
`categories`) are pure manifest -> response-shape coercion, so they belong
with the shapes they build rather than with the routes that return them.
Both are deliberately tolerant: `read_manifest` guarantees only
`id`/`category`/`filename`, and a hand-edited or older manifest must not 500
the whole listing over one odd field.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from lib.crew.mascot_library import Manifest, slugify


class CategorySummary(BaseModel):
    """One tag, with how many photos currently carry it."""

    slug: str
    label: str
    count: int


class LibraryImage(BaseModel):
    """A manifest entry as the UI consumes it, plus its `raw` URL."""

    id: str
    category: str
    filename: str
    content_type: str
    bytes: int = 0
    width: int = 0
    height: int = 0
    source: str = ""  # how it got here; "upload" for everything written today
    label: str = ""  # display name; the uploader's filename by default
    approved_at: str | None = None
    added_at: str | None = None
    url: str = ""  # serving URL for the bytes (see `image_url`)


# Envelope for ``GET /api/v1/mascot-library``. Unpaginated on purpose: the
# library is a handful of curated photos, not a feed, and the UI renders every
# tag alongside every image at once. Kept out of the docstring because
# FastAPI copies docstrings into `components/schemas.*.description`, and the
# frontend phase generates its client from that committed schema.
class LibraryResponse(BaseModel):
    """The whole library in one payload -- it is a handful of photos."""

    categories: list[CategorySummary]
    images: list[LibraryImage]


class CategoryCreate(BaseModel):
    """Request body for declaring a new tag."""

    label: str


class CategoryCreated(BaseModel):
    """The declared tag, slugified."""

    slug: str
    label: str


class CategorySuggestion(BaseModel):
    """Advisory tag for an about-to-be-uploaded photo. `None` = no opinion."""

    suggested_category: str | None = None


def image_url(image_id: str) -> str:
    """Serving URL for one photo's bytes (`GET .../images/{id}/raw`)."""
    return f"/api/v1/mascot-library/images/{image_id}/raw"


def to_model(entry: dict[str, Any]) -> LibraryImage:
    """Manifest entry -> response model, tolerating missing/odd fields."""

    def number(key: str) -> int:
        value = entry.get(key)
        return int(value) if isinstance(value, int | float) else 0

    def text(key: str) -> str | None:
        value = entry.get(key)
        return str(value) if value is not None else None

    image_id = str(entry["id"])
    return LibraryImage(
        id=image_id,
        category=str(entry["category"]),
        filename=str(entry["filename"]),
        content_type=str(entry.get("content_type") or "application/octet-stream"),
        bytes=number("bytes"),
        width=number("width"),
        height=number("height"),
        source=str(entry.get("source") or ""),
        label=str(entry.get("label") or ""),
        approved_at=text("approved_at"),
        added_at=text("added_at"),
        url=image_url(image_id),
    )


def categories(manifest: Manifest) -> list[CategorySummary]:
    """Declared tags first (creation order), then any tag only an image uses.

    A freshly declared tag with no photos yet is still listed, at `count: 0` --
    that is the state an operator uploads INTO, so hiding it would make the
    selector empty exactly when it is needed.
    """
    counts: dict[str, int] = {}
    for image in manifest["images"]:
        slug = slugify(str(image.get("category", "")))
        if slug:
            counts[slug] = counts.get(slug, 0) + 1

    summaries: list[CategorySummary] = []
    seen: set[str] = set()
    for category in manifest["categories"]:
        slug = slugify(str(category.get("slug", "")))
        if not slug or slug in seen:
            continue
        seen.add(slug)
        label = str(category.get("label") or slug)
        summaries.append(CategorySummary(slug=slug, label=label, count=counts.get(slug, 0)))
    for slug in sorted(counts.keys() - seen):
        summaries.append(
            CategorySummary(slug=slug, label=slug.replace("-", " "), count=counts[slug])
        )
    return summaries
