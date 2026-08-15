"""Pydantic output contract for the CrewAI affiliate product-selector agent.

One `Task` target (see `lib.crew.products.agent`): `ProductSelection` -- the
model's picks from the brand's REAL affiliate product catalog, never an
invented key, and legitimately empty when nothing genuinely fits the post.
Same rationale as `lib.crew.categorizer.models.CategoryChoice`: CrewAI's
`output_pydantic` requires a `BaseModel`, though this pipeline parses raw
JSON manually instead (see `lib.crew.products.execute`).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SelectedProduct(BaseModel):
    """One affiliate product the selector agent picked for a post."""

    key: str = Field(
        description="The product's catalog key, copied EXACTLY as given in the provided "
        "candidate list (character-for-character). Never invent a key not in that list."
    )
    reason: str = Field(
        description="One short sentence on why a reader of THIS specific post would "
        "genuinely want this product."
    )


class ProductSelection(BaseModel):
    """The selector agent's full pick: zero or more products from the real catalog."""

    products: list[SelectedProduct] = Field(
        default_factory=list,
        description="The selected products, at most the requested maximum. An EMPTY list "
        "is a valid, correct answer when nothing in the candidate list genuinely fits.",
    )


class ShoppingQueries(BaseModel):
    """Buyer-intent search queries for one post (see `lib.crew.products.discovery`).

    A second `Task` target in this package. Separate from `ProductSelection`
    because it answers a different question: not "which of these products
    fits" but "what would a reader of this post actually go shopping for".

    That distinction is load-bearing, not stylistic. Searching a post's own
    keywords finds ARTICLES, not products -- live-measured on the topic that
    shipped with zero products: `"dog food recall 2026"` returned 0 Amazon
    listings, while `"dog food storage container"` and `"limited ingredient
    dog food"` -- the things someone reading about a recall would actually
    buy -- returned 4 each. Deriving the queries mechanically from the brief's
    keywords reproduces the 0; understanding reader intent is the whole job.
    """

    queries: list[str] = Field(
        default_factory=list,
        description="2-4 short Amazon-style product searches a reader of THIS post would "
        "run. Name PRODUCT CATEGORIES people buy ('dog food storage container', "
        "'limited ingredient dog food'), never the post's topic or headline "
        "('dog food recall 2026'). No brand names, no 'best', no year, no site: "
        "operators. An EMPTY list is correct when the post is purely "
        "informational and a reader would buy nothing.",
    )
