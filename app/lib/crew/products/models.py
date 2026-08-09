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
