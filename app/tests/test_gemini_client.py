"""Tests for gemini_client — the shared Gemini transport + parsers.

No network: ``httpx.post`` is monkeypatched. LANGFUSE_* is left unset so
``trace_llm_call`` no-ops to a plain call. Covers both the plain-text
``_call_gemini`` and the schema-driven ``call_json``, which share
``_gemini_request``/``_first_candidate_text``. Provider selection (and the
engage/decline envelope built on top of ``call_json``) is not this module's
business any more -- see tests/test_llm_client.py and tests/test_draft_helper.py.
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


# The engage envelope its callers use, as a stand-in caller-supplied schema.
_ENGAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "engage": {"type": "boolean"},
        "comment": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["engage", "comment", "reason"],
}


def _call_json(prompt: str = "prompt", **kwargs: object) -> dict | None:
    return gc.call_json(prompt, schema=_ENGAGE_SCHEMA, max_tokens=400, **kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- call_json


def test_call_json_returns_none_without_key() -> None:
    # _clean_env autouse fixture already unsets GEMINI_API_KEY.
    assert _call_json() is None


def test_call_json_parses_valid_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    body = _candidate_text('{"engage": true, "comment": "hi", "reason": "good fit"}')
    monkeypatch.setattr(gc.httpx, "post", _fake_post(body))

    result = _call_json()

    assert result == {"engage": True, "comment": "hi", "reason": "good fit"}


def test_call_json_returns_none_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(gc.httpx, "post", _fake_post({}, status_code=500))

    assert _call_json() is None


def test_call_json_returns_none_on_malformed_json_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    body = _candidate_text("not actually json")
    monkeypatch.setattr(gc.httpx, "post", _fake_post(body))

    assert _call_json() is None


def test_call_json_returns_none_on_non_object_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid-JSON non-object (list/scalar) is still not a usable response."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(gc.httpx, "post", _fake_post(_candidate_text('["not", "an", "object"]')))

    assert _call_json() is None


def test_call_json_returns_none_on_no_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(gc.httpx, "post", _fake_post({"candidates": []}))

    assert _call_json() is None


def test_call_json_sends_response_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """The JSON call must set responseMimeType + responseSchema on the payload."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    seen: dict[str, object] = {}

    def _capture(*_args: object, **kwargs: object) -> _FakeResponse:
        seen["json"] = kwargs.get("json")
        return _FakeResponse(
            200, _candidate_text('{"engage": false, "comment": "", "reason": "x"}')
        )

    monkeypatch.setattr(gc.httpx, "post", _capture)
    _call_json()

    cfg = seen["json"]["generationConfig"]  # type: ignore[index]
    assert cfg["responseMimeType"] == "application/json"
    assert cfg["responseSchema"] is _ENGAGE_SCHEMA


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


# --------------------------------------------------------------------------- system= kwarg


def _capture_payload(monkeypatch: pytest.MonkeyPatch, body: dict) -> dict[str, object]:
    seen: dict[str, object] = {}

    def _capture(*_args: object, **kwargs: object) -> _FakeResponse:
        seen["json"] = kwargs.get("json")
        return _FakeResponse(200, body)

    monkeypatch.setattr(gc.httpx, "post", _capture)
    return seen


def test_call_gemini_system_sends_system_instruction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    seen = _capture_payload(monkeypatch, _candidate_text("hi"))

    gc._call_gemini("user prompt", system="standing rules")

    assert seen["json"]["systemInstruction"] == {  # type: ignore[index]
        "parts": [{"text": "standing rules"}]
    }


def test_call_json_system_sends_system_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """system= must coexist with the JSON response schema on the same payload."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    body = _candidate_text('{"engage": false, "comment": "", "reason": "x"}')
    seen = _capture_payload(monkeypatch, body)

    _call_json("user prompt", system="standing rules")

    payload = seen["json"]
    assert payload["systemInstruction"] == {"parts": [{"text": "standing rules"}]}  # type: ignore[index]
    assert payload["generationConfig"]["responseSchema"] is _ENGAGE_SCHEMA  # type: ignore[index]


def test_payloads_omit_system_instruction_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backward compatibility: without system=, payloads are unchanged."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    seen = _capture_payload(monkeypatch, _candidate_text("hi"))

    gc._call_gemini("prompt")
    assert "systemInstruction" not in seen["json"]  # type: ignore[operator]

    _call_json()
    assert "systemInstruction" not in seen["json"]  # type: ignore[operator]


def test_tracing_receives_dict_input_when_system_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(gc.httpx, "post", _fake_post(_candidate_text("hi")))
    seen: dict[str, object] = {}

    def _fake_trace(name: str, *, model: str, input_text: object, call: object) -> object:
        seen["input_text"] = input_text
        return call()  # type: ignore[operator]

    monkeypatch.setattr(gc, "trace_llm_call", _fake_trace)

    gc._call_gemini("user prompt", system="sys rules")
    assert seen["input_text"] == {"system": "sys rules", "user": "user prompt"}

    gc._call_gemini("user prompt")
    assert seen["input_text"] == "user prompt"  # plain string without system=
