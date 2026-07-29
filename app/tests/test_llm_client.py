"""Tests for lib.llm_client — the provider-agnostic text-LLM seam. No network.

Three concerns: the selection rule (explicit arg > ``VOICE_PROVIDER`` > key
auto-detect), which must stay identical to
``recipe-publisher/generators/drafter.py``'s; ``GeminiLLM`` full-stack down to
a monkeypatched ``httpx.post`` (system/temperature/max_tokens/schema must reach
the payload ``lib.gemini_client`` builds); and ``AnthropicLLM`` against a fake
SDK client (``system=`` lands as the Messages-API param, every failure degrades
to ``None``). Env hygiene (``delenv``) is autouse: an ambient
ANTHROPIC_API_KEY on a dev box must never make these tests pick a live provider.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from lib.llm_client import (
    AnthropicLLM,
    GeminiLLM,
    LLMRequest,
    TextLLM,
    get_llm,
    resolve_provider,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "VOICE_PROVIDER",
        "GEMINI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_MODEL",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_PUBLIC_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


# --------------------------------------------------------------------- factory


class TestFactoryExplicitProvider:
    def test_gemini(self) -> None:
        assert isinstance(get_llm("gemini"), GeminiLLM)

    def test_anthropic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")  # constructor needs it
        with patch("anthropic.Anthropic"):
            assert isinstance(get_llm("anthropic"), AnthropicLLM)

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown VOICE_PROVIDER"):
            get_llm("openai")


class TestFactoryEnvSelection:
    def test_voice_provider_env_wins_over_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VOICE_PROVIDER", "gemini")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")  # would auto-pick anthropic
        assert isinstance(get_llm(), GeminiLLM)

    def test_explicit_arg_wins_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VOICE_PROVIDER", "anthropic")
        assert isinstance(get_llm("gemini"), GeminiLLM)

    def test_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VOICE_PROVIDER", "GEMINI")
        assert isinstance(get_llm(), GeminiLLM)

    def test_unknown_env_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VOICE_PROVIDER", "llama")
        with pytest.raises(ValueError, match="unknown VOICE_PROVIDER"):
            get_llm()


class TestAutoDetect:
    """Same rule as generators/drafter._auto_detect_provider — one dialect."""

    def test_gemini_when_only_gemini_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "x")
        assert resolve_provider() == "gemini"

    def test_anthropic_when_only_anthropic_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        assert resolve_provider() == "anthropic"

    def test_anthropic_when_both_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "x")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "y")
        assert resolve_provider() == "anthropic"

    def test_gemini_when_neither_set(self) -> None:
        assert resolve_provider() == "gemini"


def test_both_implementations_satisfy_the_protocol() -> None:
    gemini: TextLLM = GeminiLLM()
    anthropic: TextLLM = AnthropicLLM(client=object())
    for impl in (gemini, anthropic):
        assert callable(impl.complete)
        assert callable(impl.complete_json)


# -------------------------------------------------------------------- GeminiLLM


def _candidate_text(text: str) -> dict[str, Any]:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


class _FakeResponse:
    def __init__(self, body: object, status_code: int = 200) -> None:
        self.status_code = status_code
        self._body = body
        self.text = str(body)[:200]

    def json(self) -> object:
        return self._body


def _capture_post(
    monkeypatch: pytest.MonkeyPatch, body: object, *, status_code: int = 200
) -> dict[str, Any]:
    """Patch the real httpx.post (what lib.gemini_client calls) and capture."""
    seen: dict[str, Any] = {}

    def _post(url: str, **kwargs: Any) -> _FakeResponse:
        seen["url"] = url
        seen["json"] = kwargs.get("json")
        seen["headers"] = kwargs.get("headers")
        return _FakeResponse(body, status_code)

    monkeypatch.setattr(httpx, "post", _post)
    return seen


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"engage": {"type": "boolean"}},
    "required": ["engage"],
}


class TestGeminiLLM:
    def test_complete_threads_system_temperature_and_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        seen = _capture_post(monkeypatch, _candidate_text("  drafted  "))

        out = GeminiLLM().complete(
            LLMRequest(user="u", system="standing rules", max_tokens=123, temperature=0.2)
        )

        assert out == "drafted"
        payload = seen["json"]
        assert payload["systemInstruction"] == {"parts": [{"text": "standing rules"}]}
        assert payload["contents"][0]["parts"][0]["text"] == "u"
        assert payload["generationConfig"]["temperature"] == 0.2
        assert payload["generationConfig"]["maxOutputTokens"] == 123
        # thinkingConfig is omitted by default: `thinkingBudget: 0` is rejected
        # by the Gemini 3.x family (400 INVALID_ARGUMENT — thinking cannot be
        # disabled there), and this client turns that into an empty draft that
        # is indistinguishable from the agent declining to engage.
        assert "thinkingConfig" not in payload["generationConfig"]
        assert seen["headers"] == {"x-goog-api-key": "test-key"}

    def test_complete_sends_thinking_budget_when_explicitly_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Models that still allow capping thinking opt in via the env var."""
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.setenv("GEMINI_THINKING_BUDGET", "0")
        seen = _capture_post(monkeypatch, _candidate_text("hi"))

        GeminiLLM().complete(LLMRequest(user="u"))

        assert seen["json"]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}

    def test_complete_defaults_to_drafting_temperature(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        seen = _capture_post(monkeypatch, _candidate_text("hi"))

        GeminiLLM().complete(LLMRequest(user="u"))

        assert seen["json"]["generationConfig"]["temperature"] == 0.7
        assert "systemInstruction" not in seen["json"]

    def test_complete_returns_none_without_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _capture_post(monkeypatch, _candidate_text("hi"))
        assert GeminiLLM().complete(LLMRequest(user="u")) is None

    def test_complete_returns_none_on_http_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        _capture_post(monkeypatch, {}, status_code=500)
        assert GeminiLLM().complete(LLMRequest(user="u")) is None

    def test_complete_json_sends_schema_and_returns_dict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        seen = _capture_post(monkeypatch, _candidate_text('{"engage": true}'))

        out = GeminiLLM().complete_json(
            LLMRequest(user="u", system="s", temperature=0.4), response_schema=_SCHEMA
        )

        assert out == {"engage": True}
        cfg = seen["json"]["generationConfig"]
        assert cfg["responseMimeType"] == "application/json"
        assert cfg["responseSchema"] is _SCHEMA
        assert cfg["temperature"] == 0.4
        assert seen["json"]["systemInstruction"] == {"parts": [{"text": "s"}]}

    def test_complete_json_returns_none_on_malformed_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        _capture_post(monkeypatch, _candidate_text("not json"))
        assert GeminiLLM().complete_json(LLMRequest(user="u"), response_schema=_SCHEMA) is None


# ----------------------------------------------------------------- AnthropicLLM


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeMessages:
    def __init__(self, reply: str | Exception, seen: dict[str, Any]) -> None:
        self._reply = reply
        self._seen = seen

    def create(self, **kwargs: Any) -> Any:
        self._seen.update(kwargs)
        if isinstance(self._reply, Exception):
            raise self._reply
        return type("_Msg", (), {"content": [_FakeBlock(self._reply)]})()


class _FakeClient:
    def __init__(self, reply: str | Exception, seen: dict[str, Any]) -> None:
        self.messages = _FakeMessages(reply, seen)


class TestAnthropicLLM:
    def test_complete_passes_system_as_messages_api_param(self) -> None:
        seen: dict[str, Any] = {}
        llm = AnthropicLLM(client=_FakeClient("  drafted  ", seen), model="test-model")

        out = llm.complete(
            LLMRequest(user="u", system="standing rules", max_tokens=99, temperature=0.3)
        )

        assert out == "drafted"
        assert seen["system"] == "standing rules"  # NOT folded into the user turn
        assert seen["messages"] == [{"role": "user", "content": "u"}]
        assert seen["model"] == "test-model"
        assert seen["max_tokens"] == 99
        assert seen["temperature"] == 0.3

    def test_complete_omits_system_when_absent(self) -> None:
        seen: dict[str, Any] = {}
        AnthropicLLM(client=_FakeClient("x", seen)).complete(LLMRequest(user="u"))
        assert "system" not in seen

    def test_model_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-4-9")
        assert AnthropicLLM(client=object())._model == "claude-sonnet-4-9"

    def test_model_default(self) -> None:
        assert AnthropicLLM(client=object())._model == "claude-sonnet-4-6"

    def test_complete_returns_none_on_sdk_failure(self) -> None:
        llm = AnthropicLLM(client=_FakeClient(RuntimeError("boom"), {}))
        assert llm.complete(LLMRequest(user="u")) is None

    def test_complete_returns_none_on_empty_text(self) -> None:
        llm = AnthropicLLM(client=_FakeClient("   ", {}))
        assert llm.complete(LLMRequest(user="u")) is None

    def test_complete_json_instructs_schema_and_parses_fenced_reply(self) -> None:
        seen: dict[str, Any] = {}
        reply = '```json\n{"engage": true, "comment": "hi"}\n```'
        llm = AnthropicLLM(client=_FakeClient(reply, seen))

        out = llm.complete_json(LLMRequest(user="u", system="s"), response_schema=_SCHEMA)

        assert out == {"engage": True, "comment": "hi"}
        sent = seen["messages"][0]["content"]
        assert sent.startswith("u\n\n")  # schema instruction appended, user turn intact
        assert json.dumps(_SCHEMA) in sent
        assert seen["system"] == "s"  # standing rules stay in the system slot

    def test_complete_json_returns_none_when_no_object(self) -> None:
        llm = AnthropicLLM(client=_FakeClient("sorry, I can't", {}))
        assert llm.complete_json(LLMRequest(user="u"), response_schema=_SCHEMA) is None

    def test_complete_json_returns_none_on_failure(self) -> None:
        llm = AnthropicLLM(client=_FakeClient(RuntimeError("boom"), {}))
        assert llm.complete_json(LLMRequest(user="u"), response_schema=_SCHEMA) is None
