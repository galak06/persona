"""Tests for gemini_client — the shared Gemini transport + parsers.

No network: ``httpx.post`` is monkeypatched. LANGFUSE_* is left unset so
``trace_llm_call`` no-ops to a plain call. Covers both the plain-text
``_call_gemini`` and the structured ``_call_gemini_json``, which share
``_gemini_request``/``_first_candidate_text``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

import gemini_client as gc


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)


class _FakeResponse:
    def __init__(self, status_code: int, body: object) -> None:
        self.status_code = status_code
        self._body = body
        self.text = str(body)[:200]

    def json(self) -> object:
        return self._body


def _fake_post(body: object, *, status_code: int = 200):
    def _post(*_args: object, **_kwargs: object) -> _FakeResponse:
        return _FakeResponse(status_code, body)

    return _post


def _candidate_text(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


# --------------------------------------------------------------------------- _call_gemini_json


def test_call_gemini_json_returns_none_without_key() -> None:
    # _clean_env autouse fixture already unsets GEMINI_API_KEY.
    assert gc._call_gemini_json("prompt") is None


def test_call_gemini_json_parses_valid_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    body = _candidate_text('{"engage": true, "comment": "hi", "reason": "good fit"}')
    monkeypatch.setattr(gc.httpx, "post", _fake_post(body))

    result = gc._call_gemini_json("prompt")

    assert result == {"engage": True, "comment": "hi", "reason": "good fit"}


def test_call_gemini_json_returns_none_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(gc.httpx, "post", _fake_post({}, status_code=500))

    assert gc._call_gemini_json("prompt") is None


def test_call_gemini_json_returns_none_on_malformed_json_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    body = _candidate_text("not actually json")
    monkeypatch.setattr(gc.httpx, "post", _fake_post(body))

    assert gc._call_gemini_json("prompt") is None


def test_call_gemini_json_returns_none_when_engage_field_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    body = _candidate_text('{"comment": "hi", "reason": "no engage field"}')
    monkeypatch.setattr(gc.httpx, "post", _fake_post(body))

    assert gc._call_gemini_json("prompt") is None


def test_call_gemini_json_returns_none_on_no_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(gc.httpx, "post", _fake_post({"candidates": []}))

    assert gc._call_gemini_json("prompt") is None


def test_call_gemini_json_sends_response_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """The JSON call must set responseMimeType + responseSchema on the payload."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    seen: dict[str, object] = {}

    def _capture(*_args: object, **kwargs: object) -> _FakeResponse:
        seen["json"] = kwargs.get("json")
        return _FakeResponse(
            200, _candidate_text('{"engage": false, "comment": "", "reason": "x"}')
        )

    monkeypatch.setattr(gc.httpx, "post", _capture)
    gc._call_gemini_json("prompt")

    cfg = seen["json"]["generationConfig"]  # type: ignore[index]
    assert cfg["responseMimeType"] == "application/json"
    assert cfg["responseSchema"] is gc._ENGAGE_RESPONSE_SCHEMA


# --------------------------------------------------------------------------- _call_gemini (text)


def test_call_gemini_returns_none_without_key() -> None:
    assert gc._call_gemini("prompt") is None


def test_call_gemini_returns_first_candidate_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(gc.httpx, "post", _fake_post(_candidate_text("  a plain reply  ")))

    assert gc._call_gemini("prompt") == "a plain reply"


def test_call_gemini_omits_response_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """The plain-text call must NOT constrain output to the engage schema."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    seen: dict[str, object] = {}

    def _capture(*_args: object, **kwargs: object) -> _FakeResponse:
        seen["json"] = kwargs.get("json")
        return _FakeResponse(200, _candidate_text("hi"))

    monkeypatch.setattr(gc.httpx, "post", _capture)
    gc._call_gemini("prompt")

    cfg = seen["json"]["generationConfig"]  # type: ignore[index]
    assert "responseSchema" not in cfg


def test_call_gemini_returns_none_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(gc.httpx, "post", _fake_post({}, status_code=429))

    assert gc._call_gemini("prompt") is None
