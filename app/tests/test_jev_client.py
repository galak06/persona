"""Unit tests for `lib.decisions.jev_client` + `jev_types` (httpx mocked).

No test here reaches the network: every one patches `httpx.post`, and the
API key is a fake set via monkeypatch.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from lib.decisions import jev_client
from lib.decisions.jev_types import (
    ChoiceAnswer,
    ChoiceQuestion,
    NoulAnswer,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
    parse_answers,
    usage_cost,
)

_FAKE_KEY = "test-not-a-real-key"
_QUESTIONS = {
    "relevant": NoulQuestion(instructions="About dogs?"),
    "value": ChoiceQuestion(instructions="What to add?", criteria={"a": "A", "b": "B"}),
    "grade": ScoreQuestion(instructions="How good?", criteria=("bad", "ok", "good")),
}
_BODY: dict[str, Any] = {
    "model": "typesafe/jev-1.13",
    "answers": {
        "relevant": {"type": "noul", "noul": 0.91},
        "value": {
            "type": "choice",
            "choice": "a",
            "probabilities": {"a": 0.8, "b": 0.2},
            "confidence": 0.8,
        },
        "grade": {
            "type": "score",
            "score": 1.15,
            "legend": {"0": "bad"},
            "probabilities": {"0": 0.1, "1": 0.6, "2": 0.3},
            "confidence": 0.77,
        },
    },
    "usage": {"cost": 0.00042},
}


class _Resp:
    """Minimal `httpx.Response` stand-in: the client reads status + text only."""

    def __init__(self, status: int, body: object = None, text: str | None = None) -> None:
        self.status_code = status
        if text is not None:
            self.text = text
        elif isinstance(body, Exception):
            self.text = "<<not json>>"
        else:
            self.text = json.dumps(body)


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(jev_client.API_KEY_ENV, _FAKE_KEY)
    monkeypatch.setattr(jev_client, "_missing_key_warned", False)


def _patch_post(monkeypatch: pytest.MonkeyPatch, result: object, seen: dict[str, Any]) -> None:
    def _post(url: str, **kwargs: Any) -> object:
        seen["url"] = url
        seen.update(kwargs)
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(httpx, "post", _post)


def test_success_parses_every_answer_and_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    _patch_post(monkeypatch, _Resp(200, _BODY), seen)

    result = jev_client.decide({"post_text": "hi"}, _QUESTIONS)

    assert result is not None
    assert result.answers["relevant"] == NoulAnswer(noul=0.91)
    value = result.answers["value"]
    assert isinstance(value, ChoiceAnswer)
    assert value.choice == "a" and value.confidence == 0.8
    grade = result.answers["grade"]
    assert isinstance(grade, ScoreAnswer) and grade.score == 1.15
    assert result.cost_usd == pytest.approx(0.00042)
    assert result.model == "typesafe/jev-1.13"
    assert result.raw_answers == _BODY["answers"]
    assert result.latency_ms >= 0
    # Request shape: pinned model, one request carrying all questions.
    assert seen["url"] == jev_client.JEV_URL
    assert seen["timeout"] == jev_client.HTTP_TIMEOUT
    assert seen["timeout"].connect == jev_client.CONNECT_TIMEOUT_SECONDS
    assert seen["json"]["model"] == "typesafe/jev-1.13"
    assert set(seen["json"]["questions"]) == {"relevant", "value", "grade"}
    assert seen["json"]["questions"]["value"]["type"] == "choice"
    assert seen["json"]["questions"]["grade"]["criteria"] == ["bad", "ok", "good"]
    assert seen["headers"]["Authorization"] == f"Bearer {_FAKE_KEY}"


def test_http_error_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_post(monkeypatch, _Resp(502, None, text="bad gateway"), {})
    assert jev_client.decide("state", _QUESTIONS) is None


def test_timeout_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_post(monkeypatch, httpx.ReadTimeout("slow"), {})
    assert jev_client.decide("state", _QUESTIONS) is None


def test_transport_error_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_post(monkeypatch, httpx.ConnectError("down"), {})
    assert jev_client.decide("state", _QUESTIONS) is None


def test_unexpected_exception_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_post(monkeypatch, RuntimeError("boom"), {})
    assert jev_client.decide("state", _QUESTIONS) is None


@pytest.mark.parametrize(
    "body",
    [
        ValueError("not json"),
        ["a", "list"],
        {"answers": "nope"},
        {"answers": {"relevant": {"noul": 0.5}}},  # missing two answers
        {"answers": {**_BODY["answers"], "value": {"choice": ""}}},
        {"answers": {**_BODY["answers"], "relevant": {"noul": "high"}}},
        {"answers": {**_BODY["answers"], "grade": {"score": True}}},
    ],
)
def test_malformed_response_returns_none(monkeypatch: pytest.MonkeyPatch, body: object) -> None:
    _patch_post(monkeypatch, _Resp(200, body), {})
    assert jev_client.decide("state", _QUESTIONS) is None


def test_missing_key_is_noop_and_warns_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(jev_client.API_KEY_ENV, raising=False)
    calls: list[str] = []
    warnings: list[str] = []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: calls.append("x"))
    monkeypatch.setattr(jev_client.log, "warning", lambda event, **_k: warnings.append(event))

    assert jev_client.decide("state", _QUESTIONS) is None
    assert jev_client.decide("state", _QUESTIONS) is None
    assert calls == []
    assert warnings == ["jev_api_key_missing"]
    assert jev_client.api_key_configured() is False


def test_string_state_is_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    _patch_post(monkeypatch, _Resp(200, _BODY), seen)
    jev_client.decide("x" * (jev_client.MAX_STATE_CHARS + 50), _QUESTIONS)
    assert len(seen["json"]["state"]) == jev_client.MAX_STATE_CHARS


def test_empty_questions_short_circuit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: calls.append("x"))
    assert jev_client.decide("state", {}) is None
    assert calls == []


def test_score_question_level_bounds() -> None:
    with pytest.raises(ValueError):
        ScoreQuestion(instructions="x", criteria=("only one",))
    with pytest.raises(ValueError):
        ScoreQuestion(instructions="x", criteria=tuple(str(i) for i in range(11)))


def test_parse_helpers_tolerate_missing_optional_fields() -> None:
    answers = parse_answers(
        {"v": ChoiceQuestion(instructions="x", criteria={"a": "A"})},
        {"answers": {"v": {"choice": "a", "probabilities": {"a": "bad", "b": 1}}}},
    )
    assert answers == {"v": ChoiceAnswer(choice="a", probabilities={"b": 1.0}, confidence=0.0)}
    assert usage_cost({"usage": {"cost": 0.1}}) == 0.1
    assert usage_cost({"usage": None}) is None
    assert usage_cost("nope") is None


def test_nothing_handed_to_the_tracer_carries_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Langfuse gets the traced input AND output; neither may hold the key.

    The output used to be the raw `httpx.Response`, whose Request carries the
    Authorization header into any attribute-walking serializer.
    """
    request = httpx.Request(
        "POST", jev_client.JEV_URL, headers={"Authorization": f"Bearer {_FAKE_KEY}"}
    )
    real = httpx.Response(200, json=_BODY, request=request)
    monkeypatch.setattr(httpx, "post", lambda *_a, **_k: real)
    traced: list[object] = []

    def _tracer(name: str, *, model: str, input_text: object, call: Any) -> object:
        result = call()
        traced.extend([name, model, input_text, result])
        return result

    monkeypatch.setattr(jev_client, "trace_llm_call", _tracer)
    assert jev_client.decide({"post_text": "hi"}, _QUESTIONS) is not None

    def _walk(obj: object) -> object:
        return getattr(obj, "__dict__", repr(obj))

    for item in traced:
        dumped = repr(item) + json.dumps(item, default=_walk)
        assert "Bearer" not in dumped
        assert _FAKE_KEY not in dumped
    assert not any(isinstance(item, httpx.Response) for item in traced)
