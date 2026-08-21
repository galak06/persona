"""Conditioning `lib.crew.wp_image` on real brand photos.

Split out of `test_crew_wp_image.py` (file-size discipline) when the call
learned to carry TWO reference photos: the scene the planner asked for plus
the one that anchors the brand's own mascot. What these assert is the wire
format -- one `inline_data` part per photo, in the caller's order, before the
text part -- because the prompt names the photos by position.

respx stubs the endpoints; no real Gemini call happens.
"""
# ruff: noqa: S101

from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx

from lib.crew.wp_image import ReferencePhoto, generate_wp_image
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


@respx.mock
def test_generate_wp_image_with_reference_skips_imagen_goes_straight_to_nano_pro() -> None:
    """A reference image can only be used by nano_pro (Imagen's `predict`
    endpoint has no image-input parameter) -- attempting Imagen first would
    silently succeed with a non-matching generic image and never reach
    nano_pro at all, defeating the whole point of supplying a reference."""
    fast_route = respx.post(_FAST_URL).mock(return_value=_predict_response())
    nano_route = respx.post(_NANO_PRO_URL).mock(return_value=_generate_content_response())

    img = generate_wp_image(
        "a dog running in a field",
        alt_hint="A running dog",
        reference_image_bytes=b"real-photo-bytes",
        reference_image_mime="image/png",
    )

    assert not fast_route.called
    assert nano_route.called
    assert img.provider == "nano_pro"


@respx.mock
def test_generate_wp_image_with_reference_sends_inline_image_and_matching_instruction() -> None:
    captured: dict[str, object] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return _generate_content_response()

    respx.post(_NANO_PRO_URL).mock(side_effect=_capture)

    generate_wp_image(
        "a dog running in a field",
        alt_hint="A running dog",
        mascot_name="Nalla",
        reference_image_bytes=b"real-photo-bytes",
        reference_image_mime="image/png",
    )

    parts = captured["payload"]["contents"][0]["parts"]  # type: ignore[index]
    assert parts[0]["inline_data"]["mime_type"] == "image/png"
    assert parts[0]["inline_data"]["data"] == base64.b64encode(b"real-photo-bytes").decode()
    prompt = parts[1]["text"]
    assert "reference photo" in prompt.lower()
    # Species-free since "no brand may be assumed to have a dog": the identity
    # constraint is about the SUBJECT of the attached photo, and the only
    # description of it is whatever the brand configured.
    assert "exact same subject" in prompt.lower()
    assert "the mascot is Nalla" in prompt


@respx.mock
def test_a_second_reference_is_sent_as_its_own_part_after_the_first() -> None:
    """One photo cannot both hold the scene and hold the brand's mascot. The
    scene photo goes first because the clause calls it PHOTO 1 -- if the parts
    were reordered or collapsed, the prompt would describe the wrong picture."""
    captured: dict[str, object] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return _generate_content_response()

    respx.post(_NANO_PRO_URL).mock(side_effect=_capture)

    generate_wp_image(
        "a dog eating in a kitchen",
        reference_image_bytes=b"scene-photo",
        reference_image_mime="image/jpeg",
        extra_reference_images=(ReferencePhoto(b"mascot-photo", "image/png"),),
    )

    parts = captured["payload"]["contents"][0]["parts"]  # type: ignore[index]
    assert len(parts) == 3
    assert parts[0]["inline_data"]["data"] == base64.b64encode(b"scene-photo").decode()
    assert parts[0]["inline_data"]["mime_type"] == "image/jpeg"
    assert parts[1]["inline_data"]["data"] == base64.b64encode(b"mascot-photo").decode()
    assert parts[1]["inline_data"]["mime_type"] == "image/png"
    assert "text" in parts[2]


@respx.mock
def test_extra_references_are_ignored_without_a_first_one() -> None:
    """ "PHOTO 2" with no photo 1 is a prompt describing a picture that isn't
    there -- and Imagen, which the extras cannot reach, must stay first in the
    chain when the caller supplied no real reference."""
    fast_route = respx.post(_FAST_URL).mock(return_value=_predict_response())

    img = generate_wp_image(
        "a dog running in a field",
        extra_reference_images=(ReferencePhoto(b"mascot-photo", "image/png"),),
    )

    assert fast_route.called
    assert img.provider == "imagen_fast"


@respx.mock
def test_generate_wp_image_without_reference_omits_inline_data_part() -> None:
    captured: dict[str, object] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return _generate_content_response()

    respx.post(_FAST_URL).mock(return_value=httpx.Response(404, text="gone"))
    respx.post(_STANDARD_URL).mock(return_value=httpx.Response(404, text="gone"))
    respx.post(_NANO_PRO_URL).mock(side_effect=_capture)

    generate_wp_image("a dog running in a field", alt_hint="A running dog")

    parts = captured["payload"]["contents"][0]["parts"]  # type: ignore[index]
    assert len(parts) == 1
    assert "text" in parts[0]
