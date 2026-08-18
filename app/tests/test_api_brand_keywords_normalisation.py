"""Regression: the sparse `keywords` storage blob must never reach the wire.

`lib.brand_templates._render_keywords` deliberately OMITS a keyword category
the operator supplied nothing for -- an absent key means "score against the
broad DEFAULT_* list", where a present-but-empty list would shadow those
defaults and collapse every relevance score to ~0. A brand onboarded with no
keywords therefore stores `keywords: {}` (the live `dogfoodandfun` row did).

`api.brand_schemas.BrandDetail.keywords` used to be a bare `dict[str, Any]`
passthrough of that blob, so `GET /brands/dogfoodandfun` answered
`"keywords": {}`. The frontend's hand-written type declared all three lists
required, so `BrandSettings.tsx`'s `keywords.primary_keywords.join(", ")`
compiled clean and threw `Cannot read properties of undefined (reading 'join')`
during render, taking the whole page down.

These tests pin the fix at the HTTP boundary: every category is always
emitted, and an emitted empty list round-trips back to omitted-in-storage so
the sparse-storage invariant survives an edit.
"""

from __future__ import annotations

from typing import Any

import pytest
from api import brand_settings_api, brands_api
from api.brand_schemas import BrandKeywords, BrandSettingsRequest

_EMPTY = {"primary_keywords": [], "secondary_keywords": [], "competitor_mentions": []}


def _row(keywords: Any) -> dict[str, Any]:
    return {"id": "sparse-brand", "name": "Sparse Brand", "status": "provisioned", **keywords}


@pytest.mark.parametrize(
    "stored",
    [
        pytest.param({}, id="key-absent-entirely"),
        pytest.param({"keywords": {}}, id="empty-blob"),
        pytest.param({"keywords": None}, id="null-blob"),
    ],
)
def test_from_row_fills_every_category_for_a_sparse_blob(stored: dict[str, Any]) -> None:
    assert BrandKeywords.from_row(_row(stored)).model_dump() == _EMPTY


def test_from_row_preserves_supplied_categories_and_fills_the_rest() -> None:
    stored = {"keywords": {"secondary_keywords": ["gps tracker"]}}
    assert BrandKeywords.from_row(_row(stored)).model_dump() == {
        "primary_keywords": [],
        "secondary_keywords": ["gps tracker"],
        "competitor_mentions": [],
    }


def test_get_brand_emits_all_three_lists_for_an_empty_keywords_blob(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact live shape that crashed the Brand Settings page."""
    row = _row({"keywords": {}})
    monkeypatch.setattr(
        brands_api.brands_db, "get", lambda bid: row if bid == "sparse-brand" else None
    )

    resp = brands_api.get_brand("sparse-brand")

    assert resp.keywords.model_dump() == _EMPTY
    # Serialised too -- a client reads the JSON, not the model.
    assert resp.model_dump()["keywords"] == _EMPTY


def test_spec_from_row_still_sees_a_sparse_blob_as_no_keywords() -> None:
    """Normalising the RESPONSE must not turn "unset" into "set to empty" for
    provisioning: `_render_keywords` keys off falsiness, and `[]` is falsy, so
    the category stays omitted in `config.json` exactly as before."""
    spec = brands_api._spec_from_row(_row({"keywords": {}}))
    assert spec.primary_keywords == []
    assert spec.secondary_keywords == []
    assert spec.competitor_mentions == []


def test_patch_leaves_keywords_alone_when_no_category_is_sent() -> None:
    body = BrandSettingsRequest(headless=False)
    assert brand_settings_api._merge_keywords(_row({"keywords": {}}), body) is None


def test_patch_merges_onto_a_sparse_blob_without_a_key_error() -> None:
    """Editing one category on a brand whose blob has none of them."""
    body = BrandSettingsRequest(primary_keywords=["dog food"])

    merged = brand_settings_api._merge_keywords(_row({"keywords": {}}), body)

    assert merged == {
        "primary_keywords": ["dog food"],
        "secondary_keywords": [],
        "competitor_mentions": [],
    }
