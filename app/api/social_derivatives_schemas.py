"""Pydantic schemas for the product-spotlight API (``api/social_derivatives_api.py``
and ``api/social_derivatives_create_api.py``).

Split from the routes the way ``api/brand_schemas.py`` is, and for one more
reason here: these models ARE the frontend's types. ``npm run gen:api:schema &&
npm run gen:api`` turns them into ``components["schemas"][...]`` in
``frontend/src/types/openapi.ts``, which ``src/api/socialDerivatives.ts``
aliases rather than hand-writing. Renaming a field here is a breaking change to
the review page, and CI's ``check:api`` fails until the types are regenerated.

Row -> model mapping lives here too (``from_row`` / ``from_entry``, the
``BrandKeywords.from_row`` precedent) so the list route and every test build
the wire shape one way.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from lib.affiliate_resolver import ProductEntry
from lib.crew.spotlight.product_terms import is_certification_verified
from lib.crew.spotlight.prompts import DISCLOSURE_SENTENCE

#: ``content_derivatives.status`` as a closed union, so the review card can
#: switch over it exhaustively. MUST equal ``lib.derivatives_db.STATUSES``
#: (``tests/test_social_derivatives_schemas.py`` pins that): a value the table
#: holds but this union lacks would turn the whole listing into a 500.
DerivativeStatus = Literal[
    "composing",
    "queued",
    "scheduled",
    "fb_publishing",
    "fb_published",
    "ig_publishing",
    "published",
    "rejected",
    "failed",
]

#: Product-picker breadth: the post's own category (default) or the whole pool.
ProductScope = Literal["category", "all"]

#: Did the live WordPress post answer? ``"unavailable"`` is a degraded success,
#: not an error: the catalog still lists, every ``in_post`` is simply false.
PostScan = Literal["ok", "unavailable"]


def _iso(value: Any) -> str | None:
    """Timestamps on the wire are ISO-8601 (``approve`` already answers
    ``due_at.isoformat()``; ``str(datetime)`` uses a space Safari won't parse)."""
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


class SpotlightSource(BaseModel):
    """One published, in-focus post a spotlight can be made from."""

    idea_id: str
    topic: str
    wp_url: str
    category: str


class SpotlightSourcesResponse(BaseModel):
    posts: list[SpotlightSource]


class SpotlightProduct(BaseModel):
    """One catalog entry in the picker.

    ``category`` and ``notes`` are optional on ``ProductEntry`` and normalised
    to ``""`` here so the dialog never branches on null.
    """

    key: str
    asin: str
    display: str
    category: str = ""
    notes: str = ""
    # The product's ASIN is linked in the LIVE post (always false when
    # ``post_scan == "unavailable"``). False is allowed: compose then flags the
    # row `product_not_in_post` instead of failing it.
    in_post: bool = False
    # The catalog note records a registry check ("verified against ...") -- the
    # only case in which the caption may name a certification.
    certification_verified: bool = False

    @classmethod
    def from_entry(cls, entry: ProductEntry, *, in_post: bool) -> SpotlightProduct:
        return cls(
            key=entry.key,
            asin=entry.asin,
            display=entry.display,
            category=entry.category or "",
            notes=entry.notes or "",
            in_post=in_post,
            certification_verified=is_certification_verified(entry),
        )


class SpotlightProductsResponse(BaseModel):
    idea_id: str
    # The post slug the `fb-<slug>` / `ig-<slug>` campaign ids will carry.
    slug: str
    scope: ProductScope
    # The slugified category `scope="category"` filtered on ("" = brand has no
    # focus and the idea no category, so nothing was filtered out).
    category: str
    post_scan: PostScan
    # In-post products first (post link order), then by display name.
    products: list[SpotlightProduct]
    # ASINs the post links that no catalog entry carries.
    unknown_asins: list[str] = []


class CreateSpotlightRequest(BaseModel):
    idea_id: str
    product_key: str
    # Only feed posts are composable in this slice; "carousel" is reserved.
    format: Literal["feed_post"] = "feed_post"
    # Optional photo-collection override; "" = let the plan decide. A slug the
    # brand holds no photos under is a 422, never silently ignored.
    reference_category: str = ""


class CreateSpotlightResponse(BaseModel):
    id: str
    status: DerivativeStatus


class SpotlightDecisionResponse(BaseModel):
    """Answer of approve / unschedule / reject."""

    id: str
    status: DerivativeStatus
    # Set by approve only: the Facebook slot the spotlight was given.
    fb_due_at: str | None = None


class SocialDerivative(BaseModel):
    """One ``content_derivatives`` row as the review page sees it."""

    id: str
    idea_id: str
    brand_id: str
    kind: str
    format: str
    product_key: str
    product_asin: str
    product_display: str
    reference_category: str = ""
    status: DerivativeStatus
    fb_caption: str | None = None
    ig_caption: str | None = None
    comment_keyword: str | None = None
    image_alt: str | None = None
    source: str | None = None
    validation_flags: list[str] = []
    fb_due_at: str | None = None
    ig_due_at: str | None = None
    fb_page_post_url: str | None = None
    ig_post_url: str | None = None
    # Copy-paste DM links, each with its own `ascsubtag`. Never in a caption.
    fb_affiliate_url: str | None = None
    ig_affiliate_url: str | None = None
    # Machine-readable reason on a 'failed' row (e.g. `no_reference_photo`).
    error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    # Joined from the source idea.
    topic: str = ""
    wp_url: str | None = None
    # The in-flight signal: the page polls the LIST while any row is composing.
    composing: bool = False
    # Whether GET /social-derivatives/{id}/image can answer. The stored path is
    # server-internal (BRAND_DIR-relative) and deliberately not exposed.
    has_image: bool = False
    # The sentence the owner's manual DM must carry next to the link.
    dm_disclosure: str = DISCLOSURE_SENTENCE

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> SocialDerivative:
        """A ``derivatives_db.list_for_review`` / ``get`` row -> wire shape."""
        status = str(row.get("status") or "")
        return cls(
            id=str(row["id"]),
            idea_id=str(row.get("idea_id") or ""),
            brand_id=str(row.get("brand_id") or ""),
            kind=str(row.get("kind") or ""),
            format=str(row.get("format") or ""),
            product_key=str(row.get("product_key") or ""),
            product_asin=str(row.get("product_asin") or ""),
            product_display=str(row.get("product_display") or ""),
            reference_category=str(row.get("reference_category") or ""),
            status=status,  # type: ignore[arg-type]  # validated by pydantic
            fb_caption=row.get("fb_caption"),
            ig_caption=row.get("ig_caption"),
            comment_keyword=row.get("comment_keyword"),
            image_alt=row.get("image_alt"),
            source=row.get("source"),
            validation_flags=list(row.get("validation_flags") or []),
            fb_due_at=_iso(row.get("fb_due_at")),
            ig_due_at=_iso(row.get("ig_due_at")),
            fb_page_post_url=row.get("fb_page_post_url"),
            ig_post_url=row.get("ig_post_url"),
            fb_affiliate_url=row.get("fb_affiliate_url"),
            ig_affiliate_url=row.get("ig_affiliate_url"),
            error=row.get("error"),
            created_at=_iso(row.get("created_at")),
            updated_at=_iso(row.get("updated_at")),
            topic=str(row.get("topic") or ""),
            wp_url=row.get("wp_url"),
            composing=status == "composing",
            has_image=bool(row.get("image_path")),
        )


class SocialDerivativesResponse(BaseModel):
    derivatives: list[SocialDerivative]
    total: int
