"""Pydantic models for the reference-image library API.

Mirrors the `library.json` manifest entries (`lib.crew.reference_library`) for
the web UI. Kept in its own module so `reference_images_api.py` stays small --
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

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from lib.crew.reference_library import Manifest, slugify
from lib.crew.reference_retag import FAILED, RETAGGED, SKIPPED, UNCHANGED, RetagOutcome

#: The four statuses `RetagSummary` counts, named by the module that produces
#: them so a new one cannot be added there and silently uncounted here.
_RETAG_STATUSES = (RETAGGED, UNCHANGED, SKIPPED, FAILED)


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
    shows_mascot: bool = False  # does the brand's own mascot appear in THIS photo?
    shows_persona: bool = False  # does the brand's own persona (the person) appear?
    description: str = ""  # one sentence from the upload-time vision pass
    approved_at: str | None = None
    added_at: str | None = None
    url: str = ""  # serving URL for the bytes (see `image_url`)


# Envelope for ``GET /api/v1/reference-images``. Unpaginated on purpose: the
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


class ImageUpdate(BaseModel):
    """PATCH body. Every field is optional; an omitted one is left unchanged.

    `category` re-homes the photo (its id is `"<category>/<filename>"`), so a
    PATCH that changes it gets a different id back than it sent.
    """

    category: str | None = None
    shows_mascot: bool | None = None
    shows_persona: bool | None = None


class RetagResult(BaseModel):
    """What the re-tagger did to one photo.

    `new_id` differs from `image_id` whenever the category changed, because a
    re-tag moves the file and an id is `"<category>/<filename>"`.
    """

    image_id: str
    new_id: str
    status: str  # retagged | unchanged | skipped | failed
    category: str = ""
    shows_mascot: bool = False
    shows_persona: bool = False
    description: str = ""
    detail: str = ""  # why it was skipped or how it failed; "" otherwise


class RetagSummary(BaseModel):
    """The whole re-tag run: per-photo results plus the counts worth showing.

    Reported per image rather than as a single pass/fail because the run is
    deliberately resilient -- a photo whose file vanished, or whose vision call
    came back empty, is one bad row in an otherwise applied run.
    """

    total: int
    retagged: int
    unchanged: int
    skipped: int
    failed: int
    results: list[RetagResult]


def image_url(image_id: str) -> str:
    """Serving URL for one photo's bytes (`GET .../images/{id}/raw`)."""
    return f"/api/v1/reference-images/images/{image_id}/raw"


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
        shows_mascot=bool(entry.get("shows_mascot")),
        shows_persona=bool(entry.get("shows_persona")),
        description=str(entry.get("description") or ""),
        approved_at=text("approved_at"),
        added_at=text("added_at"),
        url=image_url(image_id),
    )


def retag_summary(outcomes: Sequence[RetagOutcome]) -> RetagSummary:
    """`lib.crew.reference_retag` outcomes -> the response shape.

    The counts are derived here rather than in the re-tagger, which reports
    facts and leaves the arithmetic to whoever is presenting it.
    """
    results = [
        RetagResult(
            image_id=o.image_id,
            new_id=o.new_id,
            status=o.status,
            category=o.category,
            shows_mascot=o.shows_mascot,
            shows_persona=o.shows_persona,
            description=o.description,
            detail=o.detail,
        )
        for o in outcomes
    ]
    counts = {status: sum(1 for o in outcomes if o.status == status) for status in _RETAG_STATUSES}
    return RetagSummary(total=len(results), results=results, **counts)


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
