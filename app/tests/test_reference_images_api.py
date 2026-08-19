"""Filing a photo: `POST /reference-images/images` and its vision pass.

This half of the suite covers everything that happens on the way IN -- the
upload's happy path, the four ways it is refused, and the analysis that tags
it. Everything done to a photo already in the library (list, serve, re-tag,
delete, import) is `test_reference_images_edit_api.py`.

The shared harness -- the tmp-path brand, the router-only app, and the stub
that keeps the vision model offline -- is `tests/_reference_images_fakes.py`,
which documents the wiring; the three fixtures below are the local bindings
for it.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from lib.crew.reference_library import library_root, manifest_path
from lib.crew.reference_validate import MAX_UPLOAD_BYTES
from lib.crew.reference_vision import ImageAnalysis
from tests._reference_images_fakes import (
    PREFIX,
    bind_brand,
    exploding_analysis,
    make_client,
    offline_vision,
    png,
    stub_analysis,
    upload,
    upload_untagged,
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


# ── upload happy path ────────────────────────────────────────────────────────


def test_upload_files_the_image_and_returns_its_entry(client: TestClient, brand_dir: Path) -> None:
    data = png()
    response = upload(client, data)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["category"] == "eating"
    assert body["content_type"] == "image/png"
    assert body["source"] == "upload"
    assert body["label"] == "a.png"
    assert (body["width"], body["height"]) == (300, 300)
    assert body["bytes"] == len(data)
    assert body["url"] == f"{PREFIX}/images/{body['id']}/raw"

    stored = library_root(brand_dir) / body["category"] / body["filename"]
    assert stored.read_bytes() == data
    # The uploader's filename never reaches the filesystem.
    assert stored.name != "a.png"


def test_explicit_label_overrides_the_filename(client: TestClient) -> None:
    body = upload(client, png(), name="DSC_0001.png").json()
    labelled = client.post(
        f"{PREFIX}/images",
        files={"file": ("DSC_0002.png", png((1, 2, 3)), "image/png")},
        data={"category": "eating", "label": "Nalla mid-snack"},
    ).json()

    assert body["label"] == "DSC_0001.png"
    assert labelled["label"] == "Nalla mid-snack"


def test_identical_bytes_reuploaded_are_one_id_and_one_file(
    client: TestClient, brand_dir: Path
) -> None:
    data = png()
    first = upload(client, data).json()
    second = upload(client, data, name="renamed.png").json()

    assert first["id"] == second["id"]
    assert len(list((library_root(brand_dir) / "eating").iterdir())) == 1
    manifest = json.loads(manifest_path(brand_dir).read_text(encoding="utf-8"))
    assert len(manifest["images"]) == 1


def test_missing_category_falls_back_to_general(client: TestClient) -> None:
    body = upload_untagged(client, png()).json()

    assert body["category"] == "general"


# ── upload rejections ────────────────────────────────────────────────────────


def test_text_file_is_415(client: TestClient) -> None:
    response = client.post(
        f"{PREFIX}/images",
        files={"file": ("notes.txt", b"just some text", "text/plain")},
        data={"category": "eating"},
    )

    assert response.status_code == 415


def test_pdf_magic_named_png_is_415(client: TestClient) -> None:
    """The declared name and content type are never trusted -- magic bytes are."""
    response = upload(client, b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + b"0" * 512)

    assert response.status_code == 415


def test_body_past_the_cap_is_413(client: TestClient) -> None:
    oversized = b"\x89PNG\r\n\x1a\n" + b"\x00" * (MAX_UPLOAD_BYTES + 1024)

    assert upload(client, oversized).status_code == 413


def test_tiny_image_is_422(client: TestClient) -> None:
    response = upload(client, png(size=32))

    assert response.status_code == 422


def test_empty_body_is_422(client: TestClient) -> None:
    assert upload(client, b"").status_code == 422


# ── the upload analyzes ──────────────────────────────────────────────────────


def test_upload_stores_the_models_category_flag_and_description(
    client: TestClient, brand_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (brand_dir / "config.json").write_text(
        json.dumps({"site": {"mascot_name": "Nalla", "mascot_kind": "dog"}})
    )
    client.post(f"{PREFIX}/categories", json={"label": "Eating"})
    seen: dict[str, Any] = {}
    stub_analysis(
        monkeypatch, ImageAnalysis("Nalla eating from a bowl.", "Eating", True, False), seen
    )

    body = upload_untagged(client, png()).json()

    assert body["category"] == "eating"
    assert body["shows_mascot"] is True
    assert body["description"] == "Nalla eating from a bowl."
    # The brand's own tags and mascot are what the model was asked about --
    # including what KIND of thing that mascot is, which the engine may never
    # assume for it (`lib.crew.mascot`).
    assert seen == {"categories": ["Eating"], "mascot_name": "Nalla", "mascot_kind": "dog"}
    assert client.get(PREFIX).json()["images"][0]["description"] == "Nalla eating from a bowl."


def test_a_proposed_category_is_created_and_appears_in_the_listing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_analysis(monkeypatch, ImageAnalysis("A pile of kibble.", "dog treats", False, True))

    body = upload_untagged(client, png()).json()

    assert body["category"] == "dog-treats"
    assert body["shows_mascot"] is False
    listing = client.get(PREFIX).json()["categories"]
    assert {c["slug"]: (c["label"], c["count"]) for c in listing} == {
        "dog-treats": ("dog treats", 1)
    }


def test_a_failing_analysis_still_uploads(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The vision pass is advisory: an exploding model costs the photo its
    tag, never the upload itself."""
    exploding_analysis(monkeypatch)

    response = upload_untagged(client, png())

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["category"], body["shows_mascot"], body["description"]) == ("general", False, "")


def test_an_explicit_category_overrides_the_model(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_analysis(monkeypatch, ImageAnalysis("A dog bowl.", "Eating", True, False))

    body = upload(client, png(), category="kitchen").json()

    # The caller's tag wins; the rest of the analysis is still kept.
    assert body["category"] == "kitchen"
    assert body["shows_mascot"] is True
    assert body["description"] == "A dog bowl."
