"""Tests for llm_tracing — best-effort Langfuse wrapping of live LLM calls.

No real Langfuse client is ever constructed: `_client()` is monkeypatched to
return fakes, or left untouched with LANGFUSE_SECRET_KEY/LANGFUSE_PUBLIC_KEY
unset so it short-circuits to None. The one invariant every test enforces:
`call()`'s return value and exceptions must always propagate untouched,
regardless of whether/how tracing succeeds or fails.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

import llm_tracing as lt

# langfuse is an OPTIONAL dependency. Only the test that monkeypatches
# `langfuse.get_client` actually imports the package (monkeypatch.setattr on a
# dotted string target imports the module); every other test either short-
# circuits before the import or patches `lt._client` directly.
_HAS_LANGFUSE = importlib.util.find_spec("langfuse") is not None
requires_langfuse = pytest.mark.skipif(
    not _HAS_LANGFUSE, reason="langfuse not installed (optional dependency)"
)


@pytest.fixture(autouse=True)
def _no_langfuse_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)


class _FakeGeneration:
    def __init__(self) -> None:
        self.updates: list[dict[str, object]] = []

    def update(self, **kwargs: object) -> None:
        self.updates.append(kwargs)


class _FakeCM:
    def __init__(self, generation: _FakeGeneration, *, fail_enter: bool = False) -> None:
        self._generation = generation
        self._fail_enter = fail_enter
        self.exits: list[tuple[object, object, object]] = []

    def __enter__(self) -> _FakeGeneration:
        if self._fail_enter:
            raise RuntimeError("langfuse unreachable")
        return self._generation

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        self.exits.append((exc_type, exc, tb))
        return False


class _FakeClient:
    def __init__(self, cm: _FakeCM) -> None:
        self._cm = cm
        self.calls: list[dict[str, object]] = []

    def start_as_current_observation(self, **kwargs: object) -> _FakeCM:
        self.calls.append(kwargs)
        return self._cm


# --------------------------------------------------------------------------- _client


def test_client_returns_none_without_both_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    # LANGFUSE_PUBLIC_KEY intentionally left unset.
    assert lt._client() is None


@requires_langfuse
def test_client_returns_none_when_get_client_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")

    def _boom() -> None:
        raise RuntimeError("bad credentials")

    monkeypatch.setattr("langfuse.get_client", _boom)
    assert lt._client() is None


# --------------------------------------------------------------------------- trace_llm_call — untraced


def test_untraced_returns_call_result_directly() -> None:
    out = lt.trace_llm_call("x", model="gemini-2.5-flash", input_text="hi", call=lambda: "result")
    assert out == "result"


def test_untraced_propagates_call_exception() -> None:
    def _boom() -> str:
        raise ValueError("network down")

    with pytest.raises(ValueError, match="network down"):
        lt.trace_llm_call("x", model="gemini-2.5-flash", input_text="hi", call=_boom)


# --------------------------------------------------------------------------- trace_llm_call — traced


def test_traced_records_output_and_closes_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    generation = _FakeGeneration()
    cm = _FakeCM(generation)
    client = _FakeClient(cm)
    monkeypatch.setattr(lt, "_client", lambda: client)

    out = lt.trace_llm_call(
        "gemini-draft", model="gemini-2.5-flash", input_text="hi", call=lambda: "hello"
    )

    assert out == "hello"
    assert client.calls[0]["name"] == "gemini-draft"
    assert client.calls[0]["as_type"] == "generation"
    assert client.calls[0]["model"] == "gemini-2.5-flash"
    assert client.calls[0]["input"] == "hi"
    assert generation.updates == [{"output": "hello"}]
    assert cm.exits == [(None, None, None)]


def test_traced_propagates_call_exception_and_still_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    generation = _FakeGeneration()
    cm = _FakeCM(generation)
    client = _FakeClient(cm)
    monkeypatch.setattr(lt, "_client", lambda: client)

    def _boom() -> str:
        raise ValueError("network down")

    with pytest.raises(ValueError, match="network down"):
        lt.trace_llm_call("gemini-draft", model="gemini-2.5-flash", input_text="hi", call=_boom)

    # No output was ever recorded for a call that never returned.
    assert generation.updates == []
    # __exit__ was still invoked, with the real exception info -- Langfuse
    # gets a chance to mark the trace as errored.
    assert len(cm.exits) == 1
    exc_type, exc, _tb = cm.exits[0]
    assert exc_type is ValueError
    assert str(exc) == "network down"


def test_traced_falls_back_when_start_observation_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    generation = _FakeGeneration()
    cm = _FakeCM(generation, fail_enter=True)
    client = _FakeClient(cm)
    monkeypatch.setattr(lt, "_client", lambda: client)

    out = lt.trace_llm_call(
        "gemini-draft", model="gemini-2.5-flash", input_text="hi", call=lambda: "hello"
    )

    assert out == "hello"


def test_traced_call_result_returned_even_if_update_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BrokenGeneration(_FakeGeneration):
        def update(self, **kwargs: object) -> None:
            raise RuntimeError("langfuse write failed")

    cm = _FakeCM(_BrokenGeneration())
    client = _FakeClient(cm)
    monkeypatch.setattr(lt, "_client", lambda: client)

    out = lt.trace_llm_call(
        "gemini-draft", model="gemini-2.5-flash", input_text="hi", call=lambda: "hello"
    )

    assert out == "hello"
