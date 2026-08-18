"""Everything done to a photo already in the library.

The other half of `api.reference_images_api`'s coverage: declaring tags and
listing them with counts, serving the bytes, the `:path` containment guard,
deleting, importing the brand's legacy asset, and the PATCH that corrects what
the vision pass decided. Filing a photo in the first place -- upload, its
rejections, the analysis that tags it -- is `test_reference_images_api.py`.

PATCH is the reason this file exists as more than a size split: re-tagging
MOVES the file and changes the id (`"<category>/<filename>"`), so the tests
below assert on where the bytes ended up and which id serves them, not just on
the response body.

The shared harness -- the tmp-path brand, the router-only app, and the stub
that keeps the vision model offline -- is `tests/_reference_images_fakes.py`,
which documents the wiring; the three fixtures below are the local bindings
for it.
"""
# ruff: noqa: S101

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lib.crew.reference_library import library_root
from tests._reference_images_fakes import (
    PREFIX,
    bind_brand,
    make_client,
    offline_vision,
    png,
    upload,
)


@pytest.fixture
def brand_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A bare brand directory wired in as the active brand."""
    return bind_brand(tmp_path, monkeypatch)


@pytest.fixture(autouse=True)
def _offline_vision(monkeypatch: pytest.MonkeyPatch) -> None:
    offline_vision(monkeypatch)


@pytest.fixture
def client(brand_dir: Path) -> TestClient:
    return make_client()


# ── categories + listing ─────────────────────────────────────────────────────


def test_create_category_slugifies_the_label(client: TestClient) -> None:
    response = client.post(f"{PREFIX}/categories", json={"label": "On A Walk"})

    assert response.status_code == 200
    assert response.json() == {"slug": "on-a-walk", "label": "On A Walk"}


def test_unslugifiable_category_label_is_400(client: TestClient) -> None:
    assert client.post(f"{PREFIX}/categories", json={"label": "!!!"}).status_code == 400


def test_list_returns_categories_with_counts(client: TestClient) -> None:
    client.post(f"{PREFIX}/categories", json={"label": "Walking"})
    upload(client, png((1, 1, 1)), category="eating")
    upload(client, png((2, 2, 2)), category="eating")

    body = client.get(PREFIX).json()

    counts = {c["slug"]: c["count"] for c in body["categories"]}
    assert counts == {"walking": 0, "eating": 2}
    assert {c["slug"]: c["label"] for c in body["categories"]}["walking"] == "Walking"
    assert len(body["images"]) == 2
    assert {i["category"] for i in body["images"]} == {"eating"}


def test_list_is_empty_for_a_brand_with_no_library(client: TestClient) -> None:
    assert client.get(PREFIX).json() == {"categories": [], "images": []}


# ── raw bytes ────────────────────────────────────────────────────────────────


def test_raw_serves_the_bytes_uncached(client: TestClient) -> None:
    data = png()
    entry = upload(client, data).json()

    response = client.get(f"{PREFIX}/images/{entry['id']}/raw")

    assert response.status_code == 200
    assert response.content == data
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store"


def test_raw_for_an_unknown_id_is_404(client: TestClient) -> None:
    assert client.get(f"{PREFIX}/images/eating/nope.png/raw").status_code == 404


# ── delete + the containment guard ───────────────────────────────────────────


def test_delete_removes_the_image_then_404s(client: TestClient, brand_dir: Path) -> None:
    entry = upload(client, png()).json()
    stored = library_root(brand_dir) / entry["category"] / entry["filename"]

    assert client.delete(f"{PREFIX}/images/{entry['id']}").status_code == 204
    assert not stored.exists()
    assert client.delete(f"{PREFIX}/images/{entry['id']}").status_code == 404
    assert client.get(PREFIX).json()["images"] == []


@pytest.mark.parametrize(
    "image_id",
    ["..%2f..%2f..%2fetc%2fpasswd", "general%2f..%2f..%2f..%2fetc%2fpasswd"],
)
def test_traversal_id_is_400(client: TestClient, image_id: str) -> None:
    assert client.delete(f"{PREFIX}/images/{image_id}").status_code == 400
    assert client.get(f"{PREFIX}/images/{image_id}/raw").status_code == 400


# ── legacy import ────────────────────────────────────────────────────────────


def test_import_legacy_is_404_without_an_asset(client: TestClient) -> None:
    assert client.post(f"{PREFIX}/import-legacy").status_code == 404


def test_import_legacy_copies_and_leaves_the_original_untouched(
    client: TestClient, brand_dir: Path
) -> None:
    legacy = brand_dir / "data" / "assets" / "persona_mascot_reference.png"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    data = png((9, 9, 9))
    legacy.write_bytes(data)
    before = legacy.stat().st_mtime_ns

    body = client.post(f"{PREFIX}/import-legacy").json()

    assert body["category"] == "general"
    assert body["source"] == "upload"
    copied = library_root(brand_dir) / body["category"] / body["filename"]
    assert copied.read_bytes() == data
    # The original remains the last-resort fallback: still there, unmodified.
    assert legacy.is_file()
    assert legacy.read_bytes() == data
    assert legacy.stat().st_mtime_ns == before


# ── editing a filed photo ────────────────────────────────────────────────────


def test_patch_retags_and_moves_the_file(client: TestClient, brand_dir: Path) -> None:
    entry = upload(client, png(), category="eating").json()
    old_path = library_root(brand_dir) / "eating" / entry["filename"]

    response = client.patch(f"{PREFIX}/images/{entry['id']}", json={"category": "Kitchen Shots"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == f"kitchen-shots/{entry['filename']}"
    assert body["category"] == "kitchen-shots"
    assert not old_path.exists()
    assert (library_root(brand_dir) / "kitchen-shots" / entry["filename"]).is_file()
    # The new id is the one that now serves bytes, and the old one is gone.
    assert client.get(f"{PREFIX}/images/{body['id']}/raw").status_code == 200
    assert client.get(f"{PREFIX}/images/{entry['id']}/raw").status_code == 404


def test_patch_toggles_shows_mascot_without_moving_anything(
    client: TestClient, brand_dir: Path
) -> None:
    entry = upload(client, png(), category="eating").json()

    body = client.patch(f"{PREFIX}/images/{entry['id']}", json={"shows_mascot": True}).json()

    assert body["id"] == entry["id"]
    assert body["shows_mascot"] is True
    assert (library_root(brand_dir) / "eating" / entry["filename"]).is_file()
    assert client.get(PREFIX).json()["images"][0]["shows_mascot"] is True


def test_patch_into_a_category_that_already_holds_the_photo_merges(
    client: TestClient, brand_dir: Path
) -> None:
    """Ids are content-addressed, so the destination already holding this file
    means the two entries are the same photo -- one survives, not two."""
    data = png()
    first = upload(client, data, category="eating").json()
    upload(client, data, category="walking")

    body = client.patch(f"{PREFIX}/images/{first['id']}", json={"category": "walking"}).json()

    assert body["category"] == "walking"
    assert [i["id"] for i in client.get(PREFIX).json()["images"]] == [body["id"]]
    assert len(list((library_root(brand_dir) / "walking").iterdir())) == 1


def test_patch_on_an_unknown_id_is_404(client: TestClient) -> None:
    response = client.patch(f"{PREFIX}/images/eating/nope.png", json={"shows_mascot": True})

    assert response.status_code == 404


def test_patch_with_a_traversal_id_is_400(client: TestClient) -> None:
    response = client.patch(
        f"{PREFIX}/images/general%2f..%2f..%2f..%2fetc%2fpasswd", json={"category": "eating"}
    )

    assert response.status_code == 400
