"""`api.social_derivatives_schemas` -- the wire shapes the generated frontend
types are built from. No database, no app: models only."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import get_args

from api.social_derivatives_schemas import (
    DerivativeStatus,
    SocialDerivative,
    SpotlightProduct,
)

from lib import derivatives_db
from lib.affiliate_resolver import ProductEntry
from lib.crew.spotlight.prompts import DISCLOSURE_SENTENCE


def test_status_union_is_exactly_the_table_vocabulary() -> None:
    # A status the table can hold but the union lacks would 500 the whole
    # listing on response validation -- so the two may never drift.
    assert get_args(DerivativeStatus) == derivatives_db.STATUSES


def _row(**over: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": "d1",
        "idea_id": "i1",
        "brand_id": "ci_brand",
        "kind": "product_spotlight",
        "format": "feed_post",
        "product_key": "greenies-dental-treats",
        "product_asin": "B006W6YHHI",
        "product_display": "Greenies Regular Dental Dog Treats",
        "reference_category": "",
        "status": "queued",
        "image_path": "state/social_posts_pending/spotlight-d1.jpg",
        "validation_flags": None,
        "fb_due_at": None,
        "topic": "Dental chews that work",
        "wp_url": "https://example.com/dental-chews/",
    }
    row.update(over)
    return row


def test_from_row_maps_a_queued_row() -> None:
    model = SocialDerivative.from_row(_row())
    assert model.status == "queued"
    assert model.composing is False
    assert model.has_image is True
    assert model.validation_flags == []
    assert model.topic == "Dental chews that work"
    assert model.dm_disclosure == DISCLOSURE_SENTENCE
    # The server-internal path is never on the wire.
    assert "image_path" not in model.model_dump()


def test_from_row_flags_composing_and_serialises_timestamps_as_iso() -> None:
    due = datetime(2026, 9, 18, 13, 0, tzinfo=UTC)
    model = SocialDerivative.from_row(_row(status="composing", image_path=None, fb_due_at=due))
    assert model.composing is True
    assert model.has_image is False
    assert model.fb_due_at == "2026-09-18T13:00:00+00:00"


def test_product_from_entry_normalises_optionals_and_reads_the_badge() -> None:
    entry = ProductEntry(
        key="greenies-dental-treats",
        asin="B006W6YHHI",
        display="Greenies Regular Dental Dog Treats",
        category=None,
        notes="VOHC accepted. Verified against vohc.org 2026-08.",
    )
    product = SpotlightProduct.from_entry(entry, in_post=True)
    assert product.category == ""
    assert product.in_post is True
    assert product.certification_verified is True
    assert (
        SpotlightProduct.from_entry(
            ProductEntry(key="k", asin="B000000000", display="Plain Bowl"), in_post=False
        ).notes
        == ""
    )
