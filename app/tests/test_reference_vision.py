"""Tests for `lib.crew.reference_vision` -- the upload-time image analysis.

respx stubs the Gemini `generateContent` endpoint (same approach as
`tests/test_crew_wp_image.py`), so no real call happens. Three properties are
non-negotiable and get their own tests: the API key travels in the
`x-goog-api-key` HEADER and never in the URL (a `?key=` would leak it into
every access log -- a known past bug in this repo), `is_new_category` is
decided from the CALLER's list rather than believed from the model, and every
failure mode returns `None` instead of raising, because the caller is an
upload route that must file the photo either way.

A fourth property -- that the prompt never names a species the brand did not
configure -- has its own file, `tests/test_brand_agnostic_mascot.py`, because
it is the one thing here that is about the ENGINE rather than about this
module. The dog-shaped values below are one concrete FIXTURE brand (a real
brand has to be picked to test with), never an engine assumption.
"""
# ruff: noqa: S101

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest
import respx

from lib.crew.reference_vision import (
    analyze_for_brand,
    analyze_image,
    brand_mascot_name,
    vision_model,
)

_CATEGORIES = ["Eating", "Walking", "Sleeping"]


def _url() -> str:
    return (
        f"https://generativelanguage.googleapis.com/v1beta/models/{vision_model()}:generateContent"
    )


def _answer(
    category: str = "Walking", description: str = "A dog eating from a bowl.", mascot: bool = False
) -> httpx.Response:
    payload = {"description": description, "category": category, "shows_mascot": mascot}
    return httpx.Response(
        200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]}
    )


def _analyze(categories: list[str] | None = None, mascot_name: str = ""):
    return analyze_image(
        b"png",
        "image/png",
        existing_categories=_CATEGORIES if categories is None else categories,
        mascot_name=mascot_name,
    )


@pytest.fixture(autouse=True)
def _gemini_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_VISION_MODEL", raising=False)


# ── the analysis itself ──────────────────────────────────────────────────────


@respx.mock
def test_sends_the_image_inline_and_returns_every_field() -> None:
    route = respx.post(_url()).mock(
        return_value=_answer("Walking", "Nalla on a forest trail.", mascot=True)
    )

    analysis = analyze_image(
        b"\x89PNG-bytes", "image/png", existing_categories=_CATEGORIES, mascot_name="Nalla"
    )

    assert analysis is not None
    assert analysis.description == "Nalla on a forest trail."
    assert analysis.category == "Walking"
    assert analysis.shows_mascot is True
    assert analysis.is_new_category is False

    payload = json.loads(route.calls[0].request.content)
    parts = payload["contents"][0]["parts"]
    inline = parts[0]["inline_data"]
    assert inline["mime_type"] == "image/png"
    assert base64.b64decode(inline["data"]) == b"\x89PNG-bytes"
    # Both halves of the question have to reach the model: the tags it may
    # reuse, and the mascot it is being asked to recognise.
    assert "Eating" in parts[1]["text"]
    assert "Nalla" in parts[1]["text"]


@respx.mock
def test_an_unnamed_mascot_is_still_asked_about() -> None:
    route = respx.post(_url()).mock(return_value=_answer())

    assert _analyze(mascot_name="") is not None

    prompt = json.loads(route.calls[0].request.content)["contents"][0]["parts"][1]["text"]
    assert "shows_mascot" in prompt
    assert "mascot" in prompt


@respx.mock
def test_an_existing_category_is_reused_in_the_callers_spelling() -> None:
    respx.post(_url()).mock(return_value=_answer("  walking  "))

    analysis = _analyze()

    assert analysis is not None
    assert (analysis.category, analysis.is_new_category) == ("Walking", False)


@respx.mock
def test_a_proposed_label_is_flagged_as_new() -> None:
    respx.post(_url()).mock(return_value=_answer("dog treats"))

    analysis = _analyze()

    assert analysis is not None
    assert (analysis.category, analysis.is_new_category) == ("dog treats", True)


@respx.mock
def test_novelty_is_computed_not_believed() -> None:
    """One identical answer is "new" or "existing" purely by the CALLER's
    list -- the model is neither asked about it nor trusted on it."""
    respx.post(_url()).mock(return_value=_answer("kitchen counter"))

    unknown = _analyze(categories=["Eating"])
    known = _analyze(categories=["Kitchen Counter"])

    assert unknown is not None and unknown.is_new_category is True
    assert known is not None and known.is_new_category is False
    assert known.category == "Kitchen Counter"


@respx.mock
def test_an_empty_category_falls_back_to_general() -> None:
    respx.post(_url()).mock(return_value=_answer("", "A bag of kibble."))

    analysis = _analyze()

    assert analysis is not None
    assert (analysis.category, analysis.is_new_category) == ("general", False)
    assert analysis.description == "A bag of kibble."


@respx.mock
def test_a_brand_with_no_categories_still_gets_an_answer() -> None:
    respx.post(_url()).mock(return_value=_answer("ingredients"))

    analysis = _analyze(categories=[])

    assert analysis is not None
    assert (analysis.category, analysis.is_new_category) == ("ingredients", True)


@respx.mock
def test_missing_fields_degrade_rather_than_raise() -> None:
    respx.post(_url()).mock(
        return_value=httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}
        )
    )

    analysis = _analyze()

    assert analysis is not None
    assert (analysis.description, analysis.category, analysis.shows_mascot) == (
        "",
        "general",
        False,
    )


# ── the key never leaks ──────────────────────────────────────────────────────


@respx.mock
def test_key_goes_in_the_header_and_never_in_the_url() -> None:
    route = respx.post(_url()).mock(return_value=_answer("Eating"))

    _analyze()

    request = route.calls[0].request
    assert request.headers["x-goog-api-key"] == "test-key"
    assert "test-key" not in str(request.url)
    assert "key=" not in (request.url.query.decode() or "")


# ── every failure is a None ──────────────────────────────────────────────────


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="boom"),
        httpx.Response(429, json={"error": "quota"}),
        httpx.Response(200, text="not json at all"),
        httpx.Response(200, json={"candidates": []}),
        httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "{{{"}]}}]}),
        httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "[1, 2]"}]}}]}),
        httpx.Response(200, json={"candidates": [{"content": {"parts": []}}]}),
        httpx.Response(200, json={"nonsense": True}),
    ],
)
def test_bad_responses_return_none_without_raising(response: httpx.Response) -> None:
    respx.post(_url()).mock(return_value=response)

    assert _analyze() is None


@respx.mock
def test_transport_error_returns_none() -> None:
    respx.post(_url()).mock(side_effect=httpx.ConnectTimeout("timed out"))

    assert _analyze() is None


@respx.mock
def test_missing_api_key_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    route = respx.post(_url()).mock(return_value=_answer())

    assert _analyze() is None
    assert not route.called


@respx.mock
def test_model_is_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_VISION_MODEL", "gemini-test-vision")
    route = respx.post(_url()).mock(return_value=_answer("Eating"))

    assert _analyze() is not None
    assert "gemini-test-vision" in str(route.calls[0].request.url)


# ── the brand seam ───────────────────────────────────────────────────────────


@respx.mock
def test_analyze_for_brand_supplies_the_brands_tags_and_mascot(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(json.dumps({"site": {"mascot_name": "Nalla"}}))
    library = tmp_path / "data" / "assets" / "reference_images"
    library.mkdir(parents=True)
    (library / "library.json").write_text(
        json.dumps(
            {"version": 1, "categories": [{"slug": "eating", "label": "Eating"}], "images": []}
        )
    )
    route = respx.post(_url()).mock(return_value=_answer("Eating"))

    analysis = analyze_for_brand(tmp_path, b"png", "image/png")

    assert analysis is not None and analysis.category == "Eating"
    prompt = json.loads(route.calls[0].request.content)["contents"][0]["parts"][1]["text"]
    assert "Eating" in prompt
    assert "Nalla" in prompt
    # No `mascot_kind` in that config, so nothing describes what Nalla is.
    assert "dog" not in prompt.lower()


@respx.mock
def test_analyze_for_brand_swallows_anything_the_call_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An upload must never fail because the advisory pass blew up."""
    import lib.crew.reference_vision as vision

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("gemini exploded")

    monkeypatch.setattr(vision, "analyze_image", _boom)

    assert analyze_for_brand(tmp_path, b"png", "image/png") is None


@pytest.mark.parametrize(
    "config",
    ["not json at all", json.dumps({"site": {}}), json.dumps({"site": "wrong shape"}), "{}"],
)
def test_brand_mascot_name_degrades_to_empty(tmp_path: Path, config: str) -> None:
    (tmp_path / "config.json").write_text(config)

    assert brand_mascot_name(tmp_path) == ""


def test_brand_mascot_name_without_a_config_is_empty(tmp_path: Path) -> None:
    assert brand_mascot_name(tmp_path) == ""
