"""Tests for `lib.crew.reference_vision` -- the advisory tag suggester.

respx stubs the Gemini `generateContent` endpoint (same approach as
`tests/test_crew_wp_image.py`), so no real call happens. Two properties are
non-negotiable and get their own tests: the API key travels in the
`x-goog-api-key` HEADER and never in the URL (a `?key=` would leak it into
every access log -- a known past bug in this repo), and every failure mode
returns `None` instead of raising, because the caller is a route that must
answer 200 either way.
"""
# ruff: noqa: S101

from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx

from lib.crew.reference_vision import suggest_category, vision_model

_CATEGORIES = ["Eating", "Walking", "Sleeping"]


def _url() -> str:
    return (
        f"https://generativelanguage.googleapis.com/v1beta/models/{vision_model()}:generateContent"
    )


def _answer(category: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "candidates": [{"content": {"parts": [{"text": json.dumps({"category": category})}]}}]
        },
    )


@pytest.fixture(autouse=True)
def _gemini_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_VISION_MODEL", raising=False)


@respx.mock
def test_sends_the_image_inline_and_returns_the_matched_label() -> None:
    route = respx.post(_url()).mock(return_value=_answer("Walking"))

    assert suggest_category(b"\x89PNG-bytes", "image/png", _CATEGORIES) == "Walking"

    payload = json.loads(route.calls[0].request.content)
    parts = payload["contents"][0]["parts"]
    inline = parts[0]["inline_data"]
    assert inline["mime_type"] == "image/png"
    assert base64.b64decode(inline["data"]) == b"\x89PNG-bytes"
    # The tag list has to reach the model, or "verbatim from the list" is
    # not a constraint the model can satisfy.
    assert "Eating" in parts[1]["text"]


@respx.mock
def test_key_goes_in_the_header_and_never_in_the_url() -> None:
    route = respx.post(_url()).mock(return_value=_answer("Eating"))

    suggest_category(b"png", "image/png", _CATEGORIES)

    request = route.calls[0].request
    assert request.headers["x-goog-api-key"] == "test-key"
    assert "test-key" not in str(request.url)
    assert "key=" not in (request.url.query.decode() or "")


@respx.mock
def test_matches_case_insensitively_but_returns_the_callers_spelling() -> None:
    respx.post(_url()).mock(return_value=_answer("  walking  "))

    assert suggest_category(b"png", "image/png", _CATEGORIES) == "Walking"


@respx.mock
def test_label_outside_the_supplied_list_is_discarded() -> None:
    respx.post(_url()).mock(return_value=_answer("swimming"))

    assert suggest_category(b"png", "image/png", _CATEGORIES) is None


@respx.mock
def test_explicit_no_match_is_none() -> None:
    respx.post(_url()).mock(return_value=_answer(""))

    assert suggest_category(b"png", "image/png", _CATEGORIES) is None


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="boom"),
        httpx.Response(429, json={"error": "quota"}),
        httpx.Response(200, text="not json at all"),
        httpx.Response(200, json={"candidates": []}),
        httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "{{{"}]}}]}),
        httpx.Response(200, json={"nonsense": True}),
    ],
)
def test_bad_responses_return_none_without_raising(response: httpx.Response) -> None:
    respx.post(_url()).mock(return_value=response)

    assert suggest_category(b"png", "image/png", _CATEGORIES) is None


@respx.mock
def test_transport_error_returns_none() -> None:
    respx.post(_url()).mock(side_effect=httpx.ConnectTimeout("timed out"))

    assert suggest_category(b"png", "image/png", _CATEGORIES) is None


@respx.mock
def test_no_categories_short_circuits_before_any_call() -> None:
    route = respx.post(_url()).mock(return_value=_answer("Eating"))

    assert suggest_category(b"png", "image/png", []) is None
    assert not route.called


@respx.mock
def test_missing_api_key_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    route = respx.post(_url()).mock(return_value=_answer("Eating"))

    assert suggest_category(b"png", "image/png", _CATEGORIES) is None
    assert not route.called


@respx.mock
def test_model_is_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_VISION_MODEL", "gemini-test-vision")
    route = respx.post(_url()).mock(return_value=_answer("Eating"))

    assert suggest_category(b"png", "image/png", _CATEGORIES) == "Eating"
    assert "gemini-test-vision" in str(route.calls[0].request.url)
