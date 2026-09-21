"""The products panel's routes: GET /products, POST, PATCH.

Driven through `TestClient` over a real tmp-path catalog -- nothing about the
store is mocked, so a passing selection test means the exclusivity rule really
was applied to a file. The only fake is the registry lookup for the brand's
focus category, because that is a Postgres round-trip and the rule under test
is what the route does with its answer, not how it fetches it.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from pathlib import Path

import api.products_api as api_module
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import lib.brands_db as brands_db_module
from lib.crew.products.catalog_store import catalog_path, load_raw

PREFIX = "/api/v1"
FOCUS = "Dental Care"


@pytest.fixture
def brand(tmp_path: Path) -> Path:
    """A brand whose catalog holds two dental products and one grooming one."""
    path = catalog_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [
                {
                    "key": "brush-a",
                    "asin": "B000000001",
                    "display": "Brush A",
                    "category": "dental-care",
                },
                {
                    "key": "brush-b",
                    "asin": "B000000002",
                    "display": "Brush B",
                    "category": "dental-care",
                },
                {
                    "key": "clipper",
                    "asin": "B000000003",
                    "display": "Clipper",
                    "category": "grooming",
                },
            ]
        ),
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def focus() -> dict[str, str]:
    """Mutable holder so a test can change the brand's focus mid-flight."""
    return {"value": FOCUS}


@pytest.fixture
def client(brand: Path, focus: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(api_module, "resolve_api_brand", lambda: ("testbrand", brand))
    monkeypatch.setattr(brands_db_module, "focus_category", lambda _brand_id: focus["value"])
    app = FastAPI()
    app.include_router(api_module.router, prefix=PREFIX)
    return TestClient(app)


def products(client: TestClient) -> dict[str, dict]:
    """The GET payload keyed by product key."""
    body = client.get(f"{PREFIX}/products").json()
    return {p["key"]: p for p in body["products"]}


class TestGetProducts:
    def test_reports_the_focus_context(self, client: TestClient) -> None:
        body = client.get(f"{PREFIX}/products").json()
        assert body["focus_category"] == FOCUS
        assert body["focus_slug"] == "dental-care"
        assert body["selected_count"] == 0

    def test_flags_category_membership_without_granting_selection(self, client: TestClient) -> None:
        """`in_focus_category` is advisory; `selected` is the decision."""
        rows = products(client)
        assert rows["brush-a"]["in_focus_category"] is True
        assert rows["brush-a"]["selected"] is False
        assert rows["clipper"]["in_focus_category"] is False

    def test_missing_catalog_is_an_empty_list_not_an_error(
        self, client: TestClient, brand: Path
    ) -> None:
        catalog_path(brand).unlink()
        body = client.get(f"{PREFIX}/products").json()
        assert body["products"] == []


class TestPostProduct:
    def test_creates_unselected_by_default(self, client: TestClient) -> None:
        response = client.post(
            f"{PREFIX}/products",
            json={"key": "floss-c", "asin": "B000000009", "display": "Floss C"},
        )
        assert response.status_code == 201
        assert response.json()["selected"] is False

    def test_can_create_and_take_the_focus_in_one_call(self, client: TestClient) -> None:
        client.patch(f"{PREFIX}/products/brush-a", json={"selected": True})
        response = client.post(
            f"{PREFIX}/products",
            json={
                "key": "floss-c",
                "asin": "B000000009",
                "display": "Floss C",
                "select_for_focus": True,
            },
        )
        assert response.json()["selected"] is True
        assert products(client)["brush-a"]["selected"] is False

    def test_duplicate_key_is_a_400(self, client: TestClient) -> None:
        response = client.post(f"{PREFIX}/products", json={"key": "brush-a", "asin": "B000000009"})
        assert response.status_code == 400

    def test_bad_asin_is_a_400(self, client: TestClient) -> None:
        response = client.post(f"{PREFIX}/products", json={"key": "floss-c", "asin": "nope"})
        assert response.status_code == 400


class TestPatchProduct:
    def test_selecting_displaces_the_incumbent(self, client: TestClient) -> None:
        client.patch(f"{PREFIX}/products/brush-a", json={"selected": True})
        client.patch(f"{PREFIX}/products/brush-b", json={"selected": True})
        rows = products(client)
        assert rows["brush-b"]["selected"] is True
        assert rows["brush-a"]["selected"] is False

    def test_selected_count_never_exceeds_one(self, client: TestClient) -> None:
        client.patch(f"{PREFIX}/products/brush-a", json={"selected": True})
        client.patch(f"{PREFIX}/products/brush-b", json={"selected": True})
        assert client.get(f"{PREFIX}/products").json()["selected_count"] == 1

    def test_selecting_with_no_focus_declared_is_a_409(
        self, client: TestClient, focus: dict[str, str]
    ) -> None:
        focus["value"] = ""
        response = client.patch(f"{PREFIX}/products/brush-a", json={"selected": True})
        assert response.status_code == 409

    def test_edits_and_selection_apply_together(self, client: TestClient) -> None:
        response = client.patch(
            f"{PREFIX}/products/brush-a",
            json={"notes": "vet recommended", "selected": True},
        )
        body = response.json()
        assert body["notes"] == "vet recommended"
        assert body["selected"] is True

    def test_deactivating_keeps_the_row_resolvable(self, client: TestClient, brand: Path) -> None:
        """Soft delete: the entry stays so published placeholders still work."""
        client.patch(f"{PREFIX}/products/brush-a", json={"active": False})
        assert any(e["key"] == "brush-a" for e in load_raw(brand))
        assert products(client)["brush-a"]["active"] is False

    def test_unknown_key_is_a_404(self, client: TestClient) -> None:
        response = client.patch(f"{PREFIX}/products/ghost", json={"active": False})
        assert response.status_code == 404
