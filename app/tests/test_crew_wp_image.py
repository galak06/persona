"""Tests for lib.crew.wp_image -- hero-image generation for CrewAI drafts.

Uses respx to stub the Imagen predict endpoint -- no real Gemini/Imagen
network call happens.
"""
# ruff: noqa: S101

from __future__ import annotations

import base64
from pathlib import Path

import httpx
import pytest
import respx

from lib.crew.wp_image import (
    build_image_brief,
    generate_wp_image,
    resolve_reference_image_path,
)
from lib.crew.wp_image_providers import IMAGEN_FAST, IMAGEN_STANDARD, NANO_PRO

_FAST_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{IMAGEN_FAST}:predict"
_STANDARD_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{IMAGEN_STANDARD}:predict"
_NANO_PRO_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{NANO_PRO}:generateContent"
)


@pytest.fixture(autouse=True)
def _gemini_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")


def _predict_response(png_bytes: bytes = b"fake-png") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "predictions": [
                {
                    "bytesBase64Encoded": base64.b64encode(png_bytes).decode(),
                    "mimeType": "image/png",
                }
            ]
        },
    )


def _generate_content_response(jpeg_bytes: bytes = b"fake-jpeg") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "inlineData": {
                                    "data": base64.b64encode(jpeg_bytes).decode(),
                                    "mimeType": "image/jpeg",
                                }
                            }
                        ]
                    }
                }
            ]
        },
    )


def test_build_image_brief_joins_title_and_mascot_angle() -> None:
    brief = build_image_brief(
        "Best No-Subscription GPS Trackers 2026", "Nalla wandered off during a trail run once."
    )
    assert brief == (
        "Best No-Subscription GPS Trackers 2026. Nalla wandered off during a trail run once."
    )


def test_build_image_brief_drops_empty_parts() -> None:
    assert build_image_brief("Title Only", "") == "Title Only"
    assert build_image_brief("", "") == ""


@respx.mock
def test_generate_wp_image_succeeds_via_imagen_fast() -> None:
    route = respx.post(_FAST_URL).mock(return_value=_predict_response())

    img = generate_wp_image("a dog running in a field", alt_hint="A running dog")

    assert route.called
    assert img.provider == "imagen_fast"
    assert img.bytes_ == b"fake-png"
    assert img.alt_text == "A running dog"


@respx.mock
def test_generate_wp_image_falls_back_to_imagen_standard_on_fast_failure() -> None:
    respx.post(_FAST_URL).mock(return_value=httpx.Response(500, text="fast is down"))
    route = respx.post(_STANDARD_URL).mock(return_value=_predict_response())

    img = generate_wp_image("a dog running in a field", alt_hint="A running dog")

    assert route.called
    assert img.provider == "imagen_standard"
    assert img.bytes_ == b"fake-png"


@respx.mock
def test_generate_wp_image_falls_back_to_nano_pro_when_both_imagen_tiers_fail() -> None:
    """Live-tested during this build's real-validation run: both Imagen 4
    tiers now return HTTP 404 ("no longer available to new users") for this
    project's GEMINI_API_KEY -- a real, external provider deprecation. This
    third tier (`gemini-3-pro-image-preview` via `generateContent`, matching
    `recipe-publisher/generators/image.py::_generate_nano_pro`) is what
    actually produces a real image in that environment today."""
    respx.post(_FAST_URL).mock(return_value=httpx.Response(404, text="no longer available"))
    respx.post(_STANDARD_URL).mock(return_value=httpx.Response(404, text="no longer available"))
    route = respx.post(_NANO_PRO_URL).mock(return_value=_generate_content_response())

    img = generate_wp_image("a dog running in a field", alt_hint="A running dog")

    assert route.called
    assert img.provider == "nano_pro"
    assert img.bytes_ == b"fake-jpeg"
    assert img.alt_text == "A running dog"


@respx.mock
def test_generate_wp_image_returns_placeholder_when_all_providers_fail() -> None:
    respx.post(_FAST_URL).mock(return_value=httpx.Response(500, text="fast is down"))
    respx.post(_STANDARD_URL).mock(return_value=httpx.Response(500, text="standard is down too"))
    respx.post(_NANO_PRO_URL).mock(return_value=httpx.Response(500, text="nano_pro is down too"))

    img = generate_wp_image("a dog running in a field", alt_hint="A running dog")

    assert img.provider == "none"
    assert img.url == "placeholder"
    assert img.bytes_ == b""
    assert img.alt_text == "A running dog"


def test_generate_wp_image_returns_placeholder_with_no_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    img = generate_wp_image("a dog running in a field", alt_hint="A running dog")

    assert img.provider == "none"
    assert img.bytes_ == b""


@respx.mock
def test_generate_wp_image_never_appends_food_styling() -> None:
    """This pipeline drafts general lifestyle/gear posts -- the prompt must
    never carry the recipe pipeline's dog-treat/food-photography suffix."""
    captured: dict[str, object] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        import json as _json

        captured["payload"] = _json.loads(request.content)
        return _predict_response()

    respx.post(_FAST_URL).mock(side_effect=_capture)

    generate_wp_image("a GPS tracker on a dog's collar", alt_hint="GPS tracker")

    prompt = captured["payload"]["instances"][0]["prompt"]  # type: ignore[index]
    # The style guard explicitly NEGATES food props ("no dog treats, no
    # bowls, ...") -- what must never appear is the recipe pipeline's
    # POSITIVE food-styling phrase from `generators/image.py::_STYLE_SUFFIX`.
    assert "piled in a dog's bowl" not in prompt.lower()
    assert "homemade dog treats" not in prompt.lower()
    assert "no dog treats" in prompt.lower()


# ── resolve_reference_image_path ─────────────────────────────────────────────


def test_resolve_reference_image_path_missing_returns_none(tmp_path: Path) -> None:
    assert resolve_reference_image_path(tmp_path) is None


def test_resolve_reference_image_path_finds_png(tmp_path: Path) -> None:
    assets = tmp_path / "data" / "assets"
    assets.mkdir(parents=True)
    ref = assets / "persona_mascot_reference.png"
    ref.write_bytes(b"fake-png-bytes")
    assert resolve_reference_image_path(tmp_path) == ref


def test_resolve_reference_image_path_finds_jpg(tmp_path: Path) -> None:
    assets = tmp_path / "data" / "assets"
    assets.mkdir(parents=True)
    ref = assets / "persona_mascot_reference.jpg"
    ref.write_bytes(b"fake-jpg-bytes")
    assert resolve_reference_image_path(tmp_path) == ref
