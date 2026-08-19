"""Shared harness for the `api.reference_images_api` route tests.

The suite is split in two -- `test_reference_images_api.py` covers filing a
photo (upload, its rejections, the vision pass that tags it), and
`test_reference_images_edit_api.py` covers everything done to a photo already
in the library (list, serve, re-tag, delete, import). Both drive the same app
over the same tmp-path brand, so the wiring lives here rather than being
duplicated -- and, more to the point, than being allowed to drift.

Everything is driven through `TestClient` rather than by calling the route
functions directly: `UploadFile`/`Form` are FastAPI dependency markers, so a
plain call would pass `File(...)` objects instead of a body, and multipart
parsing, status codes and the `:path` converter are exactly what needs
covering.

The brand is a `tmp_path` directory injected by monkeypatching
`resolve_api_brand` in the route module's namespace, and the app under test
mounts only this router -- nothing about the filesystem or the store is
mocked, so a passing upload test means real bytes landed on real disk.

The ONE thing that is always faked is the vision model: `offline_vision`
replaces `lib.crew.reference_vision.analyze_image` so no test can reach the
network, and "no opinion" is the default every test runs under until it asks
for otherwise. Faking it at that depth rather than at the route's
`analyze_for_brand` import keeps the brand seam (its tag list, its mascot
name) inside what is tested.

Everything here is a plain function, deliberately -- pytest fixtures IMPORTED
into a test module shadow the parameter names that request them, which reads
as a redefinition to any linter. Each test module declares its own three-line
`brand_dir`/`client`/`_offline_vision` fixtures over these builders instead.
"""
# ruff: noqa: S101

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

import api.reference_images_api as api_module
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import lib.crew.reference_vision as vision_module
from lib.crew.reference_vision import ImageAnalysis

PREFIX = "/api/v1/reference-images"


def png(color: tuple[int, int, int] = (10, 20, 30), size: int = 300) -> bytes:
    """A real PNG of `size`x`size` -- validation decodes it, so it must be one."""
    buf = BytesIO()
    Image.new("RGB", (size, size), color).save(buf, format="PNG")
    return buf.getvalue()


def bind_brand(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Wire `tmp_path` in as the active brand and hand it back."""
    monkeypatch.setattr(api_module, "resolve_api_brand", lambda: (tmp_path.name, str(tmp_path)))
    return tmp_path


def offline_vision(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test reaches Gemini. Default answer: none -- "file it untagged"."""
    monkeypatch.setattr(vision_module, "analyze_image", lambda *_a, **_k: None)


def make_client() -> TestClient:
    """An app mounting only this router -- nothing else is under test."""
    app = FastAPI()
    app.include_router(api_module.router, prefix="/api/v1")
    return TestClient(app)


def stub_analysis(
    monkeypatch: pytest.MonkeyPatch,
    analysis: ImageAnalysis | None,
    seen: dict[str, Any] | None = None,
) -> None:
    """Answer every analysis with `analysis`, recording what it was asked."""

    def _fake(
        _image: bytes,
        _content_type: str,
        *,
        existing_categories: Any,
        mascot_name: str = "",
        mascot_kind: str = "",
    ) -> ImageAnalysis | None:
        if seen is not None:
            seen["categories"] = list(existing_categories)
            seen["mascot_name"] = mascot_name
            seen["mascot_kind"] = mascot_kind
        return analysis

    monkeypatch.setattr(vision_module, "analyze_image", _fake)


def exploding_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    """A vision model that raises -- the advisory path's failure mode."""

    def _boom(*_args: object, **_kwargs: object) -> ImageAnalysis:
        raise RuntimeError("gemini exploded")

    monkeypatch.setattr(vision_module, "analyze_image", _boom)


def upload(client: TestClient, data: bytes, *, name: str = "a.png", category: str = "eating"):
    """Upload with an explicit category override (the scripted-caller path)."""
    return client.post(
        f"{PREFIX}/images",
        files={"file": (name, data, "image/png")},
        data={"category": category},
    )


def upload_untagged(client: TestClient, data: bytes, *, name: str = "a.png"):
    """Upload the way the UI does -- no category, the model picks one."""
    return client.post(f"{PREFIX}/images", files={"file": (name, data, "image/png")})
