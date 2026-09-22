"""Wire shapes for the affiliate-products panel in Brand Settings.

The panel edits one thing the operator actually thinks about -- "is this THE
product for the current focus run?" -- so `selected` is a plain boolean on the
wire, resolved against the brand's declared focus category by the route. The
stored form (`selected_for`, a list of categories) is an implementation detail
the UI never has to assemble, which is what keeps rotating the focus from
silently re-interpreting an old selection.

Selection is one-per-category: setting `selected=true` on a product takes the
category off whichever product held it. So `selected_count` is only ever 0 or
1 for a given focus, and the panel should render the choice as a radio, not a
checkbox list.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProductModel(BaseModel):
    """One catalog entry as the panel renders it."""

    key: str = Field(description="Stable id referenced by [AFFILIATE:key] placeholders")
    asin: str = Field(description="10-character Amazon product id")
    display: str = Field(description="Human-readable product name")
    category: str = Field(default="", description="Catalog category tag, e.g. dental-care")
    notes: str = Field(default="", description="Editorial 'why this product' line")
    active: bool = Field(default=True, description="False hides it from selection")
    selected_for: list[str] = Field(
        default_factory=list,
        description="Focus categories this product is explicitly selected for",
    )
    selected: bool = Field(
        default=False,
        description=(
            "Whether this is THE product selected for the brand's current "
            "focus category. At most one product carries this per category."
        ),
    )
    in_focus_category: bool = Field(
        default=False,
        description=(
            "Whether its own `category` tag matches the current focus. Advisory "
            "only -- it never grants selection, it just tells the panel which "
            "products are the obvious ones to pick."
        ),
    )


class ProductsResponse(BaseModel):
    """The whole catalog plus the focus context needed to render it."""

    focus_category: str = Field(default="", description="'' when no focus is declared")
    focus_slug: str = Field(default="", description="Comparison form of focus_category")
    products: list[ProductModel] = Field(default_factory=list)
    selected_count: int = Field(
        default=0,
        description=(
            "0 or 1 -- whether the focus category has its product chosen yet. "
            "0 means posts in this run ship with no product block at all."
        ),
    )
    max_products_per_post: int = Field(
        description=(
            "The selector's per-post ceiling, for display only. One-per-"
            "category selection means a focus run never reaches it: every post "
            "in the run promotes the single selected product."
        )
    )


class ProductCreate(BaseModel):
    """A new catalog entry."""

    key: str = Field(description="[a-z0-9][a-z0-9_-]* -- immutable once created")
    asin: str
    display: str = ""
    category: str = ""
    notes: str = ""
    select_for_focus: bool = Field(
        default=False,
        description=(
            "Make it THE product for the current focus category, displacing "
            "whichever one holds it. Defaults to false: with one product per "
            "category, adding to the catalog and swapping the live promotion "
            "are different decisions and should not share a button."
        ),
    )


class ProductUpdate(BaseModel):
    """A partial edit. `None` means 'leave this field alone'."""

    display: str | None = None
    category: str | None = None
    notes: str | None = None
    active: bool | None = None
    selected: bool | None = Field(
        default=None,
        description=(
            "true makes this THE product for the current focus category "
            "(displacing the incumbent); false leaves the category unowned."
        ),
    )
