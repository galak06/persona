"""Tests for `api.mascot_library_api` -- the operator's library endpoints.

Driven through `TestClient` rather than by calling the route functions
directly: `UploadFile`/`Form` are FastAPI dependency markers, so a plain call
would pass `File(...)` objects instead of a body, and multipart parsing,
status codes and the `:path` converter are exactly what needs covering here.

The brand is a `tmp_path` directory injected by monkeypatching
`resolve_api_brand` in the route module's namespace, and the app under test
mounts only this router -- nothing about the filesystem or the store is
mocked, so a passing upload test means real bytes landed on real disk.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import api.mascot_library_api as api_module
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from lib.crew.mascot_library import library_root, manifest_path
from lib.crew.mascot_validate import MAX_UPLOAD_BYTES

_PREFIX = "/api/v1/mascot-library"


def _png(color: tuple[int, int, int] = (10, 20, 30), size: int = 300) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (size, size), color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def brand_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A bare brand directory wired in as the active brand."""
    monkeypatch.setattr(api_module, "resolve_api_brand", lambda: (tmp_path.name, str(tmp_path)))
    return tmp_path


@pytest.fixture
def client(brand_dir: Path) -> TestClient:
    app = FastAPI()
    app.include_router(api_module.router, prefix="/api/v1")
    return TestClient(app)


def _upload(client: TestClient, data: bytes, *, name: str = "a.png", category: str = "eating"):
    return client.post(
        f"{_PREFIX}/images",
        files={"file": (name, data, "image/png")},
        data={"category": category},
    )


# ── upload happy path ────────────────────────────────────────────────────────


def test_upload_files_the_image_and_returns_its_entry(client: TestClient, brand_dir: Path) -> None:
    png = _png()
    response = _upload(client, png)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["category"] == "eating"
    assert body["content_type"] == "image/png"
    assert body["source"] == "upload"
    assert body["label"] == "a.png"
    assert (body["width"], body["height"]) == (300, 300)
    assert body["bytes"] == len(png)
    assert body["url"] == f"{_PREFIX}/images/{body['id']}/raw"

    stored = library_root(brand_dir) / body["category"] / body["filename"]
    assert stored.read_bytes() == png
    # The uploader's filename never reaches the filesystem.
    assert stored.name != "a.png"


def test_explicit_label_overrides_the_filename(client: TestClient) -> None:
    body = _upload(client, _png(), name="DSC_0001.png").json()
    labelled = client.post(
        f"{_PREFIX}/images",
        files={"file": ("DSC_0002.png", _png((1, 2, 3)), "image/png")},
        data={"category": "eating", "label": "Nalla mid-snack"},
    ).json()

    assert body["label"] == "DSC_0001.png"
    assert labelled["label"] == "Nalla mid-snack"


def test_identical_bytes_reuploaded_are_one_id_and_one_file(
    client: TestClient, brand_dir: Path
) -> None:
    png = _png()
    first = _upload(client, png).json()
    second = _upload(client, png, name="renamed.png").json()

    assert first["id"] == second["id"]
    assert len(list((library_root(brand_dir) / "eating").iterdir())) == 1
    manifest = json.loads(manifest_path(brand_dir).read_text(encoding="utf-8"))
    assert len(manifest["images"]) == 1


def test_missing_category_falls_back_to_general(client: TestClient) -> None:
    body = client.post(f"{_PREFIX}/images", files={"file": ("a.png", _png(), "image/png")}).json()

    assert body["category"] == "general"


# ── upload rejections ────────────────────────────────────────────────────────


def test_text_file_is_415(client: TestClient) -> None:
    response = client.post(
        f"{_PREFIX}/images",
        files={"file": ("notes.txt", b"just some text", "text/plain")},
        data={"category": "eating"},
    )

    assert response.status_code == 415


def test_pdf_magic_named_png_is_415(client: TestClient) -> None:
    """The declared name and content type are never trusted -- magic bytes are."""
    response = _upload(client, b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + b"0" * 512)

    assert response.status_code == 415


def test_body_past_the_cap_is_413(client: TestClient) -> None:
    oversized = b"\x89PNG\r\n\x1a\n" + b"\x00" * (MAX_UPLOAD_BYTES + 1024)

    assert _upload(client, oversized).status_code == 413


def test_tiny_image_is_422(client: TestClient) -> None:
    response = _upload(client, _png(size=32))

    assert response.status_code == 422


def test_empty_body_is_422(client: TestClient) -> None:
    assert _upload(client, b"").status_code == 422


# ── categories + listing ─────────────────────────────────────────────────────


def test_create_category_slugifies_the_label(client: TestClient) -> None:
    response = client.post(f"{_PREFIX}/categories", json={"label": "On A Walk"})

    assert response.status_code == 200
    assert response.json() == {"slug": "on-a-walk", "label": "On A Walk"}


def test_unslugifiable_category_label_is_400(client: TestClient) -> None:
    assert client.post(f"{_PREFIX}/categories", json={"label": "!!!"}).status_code == 400


def test_list_returns_categories_with_counts(client: TestClient) -> None:
    client.post(f"{_PREFIX}/categories", json={"label": "Walking"})
    _upload(client, _png((1, 1, 1)), category="eating")
    _upload(client, _png((2, 2, 2)), category="eating")

    body = client.get(_PREFIX).json()

    counts = {c["slug"]: c["count"] for c in body["categories"]}
    assert counts == {"walking": 0, "eating": 2}
    assert {c["slug"]: c["label"] for c in body["categories"]}["walking"] == "Walking"
    assert len(body["images"]) == 2
    assert {i["category"] for i in body["images"]} == {"eating"}


def test_list_is_empty_for_a_brand_with_no_library(client: TestClient) -> None:
    assert client.get(_PREFIX).json() == {"categories": [], "images": []}


# ── raw bytes ────────────────────────────────────────────────────────────────


def test_raw_serves_the_bytes_uncached(client: TestClient) -> None:
    png = _png()
    entry = _upload(client, png).json()

    response = client.get(f"{_PREFIX}/images/{entry['id']}/raw")

    assert response.status_code == 200
    assert response.content == png
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store"


def test_raw_for_an_unknown_id_is_404(client: TestClient) -> None:
    assert client.get(f"{_PREFIX}/images/eating/nope.png/raw").status_code == 404


# ── delete + the containment guard ───────────────────────────────────────────


def test_delete_removes_the_image_then_404s(client: TestClient, brand_dir: Path) -> None:
    entry = _upload(client, _png()).json()
    stored = library_root(brand_dir) / entry["category"] / entry["filename"]

    assert client.delete(f"{_PREFIX}/images/{entry['id']}").status_code == 204
    assert not stored.exists()
    assert client.delete(f"{_PREFIX}/images/{entry['id']}").status_code == 404
    assert client.get(_PREFIX).json()["images"] == []


@pytest.mark.parametrize(
    "image_id",
    ["..%2f..%2f..%2fetc%2fpasswd", "general%2f..%2f..%2f..%2fetc%2fpasswd"],
)
def test_traversal_id_is_400(client: TestClient, image_id: str) -> None:
    assert client.delete(f"{_PREFIX}/images/{image_id}").status_code == 400
    assert client.get(f"{_PREFIX}/images/{image_id}/raw").status_code == 400


# ── legacy import ────────────────────────────────────────────────────────────


def test_import_legacy_is_404_without_an_asset(client: TestClient) -> None:
    assert client.post(f"{_PREFIX}/import-legacy").status_code == 404


def test_import_legacy_copies_and_leaves_the_original_untouched(
    client: TestClient, brand_dir: Path
) -> None:
    legacy = brand_dir / "data" / "assets" / "persona_mascot_reference.png"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    png = _png((9, 9, 9))
    legacy.write_bytes(png)
    before = legacy.stat().st_mtime_ns

    body = client.post(f"{_PREFIX}/import-legacy").json()

    assert body["category"] == "general"
    assert body["source"] == "upload"
    copied = library_root(brand_dir) / body["category"] / body["filename"]
    assert copied.read_bytes() == png
    # The original remains the last-resort fallback: still there, unmodified.
    assert legacy.is_file()
    assert legacy.read_bytes() == png
    assert legacy.stat().st_mtime_ns == before


# ── category suggestion ──────────────────────────────────────────────────────


def test_suggest_category_returns_the_models_answer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.post(f"{_PREFIX}/categories", json={"label": "Eating"})
    monkeypatch.setattr(api_module, "suggest_category", lambda *a, **k: "Eating")

    response = client.post(
        f"{_PREFIX}/suggest-category", files={"file": ("a.png", _png(), "image/png")}
    )

    assert response.status_code == 200
    assert response.json() == {"suggested_category": "Eating"}


def test_suggest_category_is_null_when_the_vision_call_fails(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing model call must never become a 5xx -- the UI just leaves the
    selector empty."""

    def _boom(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("gemini exploded")

    monkeypatch.setattr(api_module, "suggest_category", _boom)

    response = client.post(
        f"{_PREFIX}/suggest-category", files={"file": ("a.png", _png(), "image/png")}
    )

    assert response.status_code == 200
    assert response.json() == {"suggested_category": None}


def test_suggest_category_validates_the_bytes_too(client: TestClient) -> None:
    response = client.post(
        f"{_PREFIX}/suggest-category", files={"file": ("a.png", b"nope", "image/png")}
    )

    assert response.status_code == 415
