"""Tests for reply_drafter — fallback paths + site-post relevance ranking.

Live Gemini calls are covered by manual smoke runs — these are fast unit
tests that exercise the non-network parts (relevance ranking, env-guard
fallbacks, voice-validation on templates).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

import reply_drafter as rd


@pytest.fixture(autouse=True)
def _no_gemini(monkeypatch):
    """Force fallback path — tests never hit the network."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def _no_langfuse(monkeypatch):
    """llm_tracing.trace_llm_call must no-op to a plain call() in tests."""
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)


def test_reply_falls_back_to_template_without_gemini() -> None:
    text = rd.draft_reply(
        our_comment="We tracked Nalla's shed volume for a month.",
        their_reply="How long before you saw a change?",
        their_author="Alex Park",
    )
    assert "Alex" in text
    assert text.rstrip().endswith("?")


def test_comment_returns_empty_string_without_gemini() -> None:
    # draft_comment has no conservative fallback — caller handles that.
    text = rd.draft_comment(
        post_text="Anyone have a good peanut butter recipe for dog treats?",
        category="food",
        group_or_hashtag="Homemade Dog Food Recipes",
    )
    assert text == ""


def test_relevant_posts_ranks_by_keyword_overlap() -> None:
    posts = [
        rd.SitePost(
            title="Spring Shedding Survival Guide",
            url="a",
            excerpt="",
            categories=["Grooming"],
            tags=["shedding", "deshedding"],
        ),
        rd.SitePost(
            title="Peanut Butter Banana Biscuits",
            url="b",
            excerpt="",
            categories=["Food"],
            tags=["recipes", "treats"],
        ),
        rd.SitePost(
            title="GPS Tracker Comparison",
            url="c",
            excerpt="",
            categories=["Gear"],
            tags=["gps", "tracker"],
        ),
    ]
    hits = rd._relevant_posts("Nalla has been shedding like crazy all spring", posts, limit=2)
    assert hits
    assert "Shedding" in hits[0].title


def test_relevant_posts_returns_empty_when_nothing_matches() -> None:
    posts = [
        rd.SitePost(
            title="GPS Tracker Comparison", url="c", excerpt="", categories=["Gear"], tags=["gps"]
        ),
    ]
    assert rd._relevant_posts("completely unrelated topic xyzzy", posts) == []


def test_strip_meta_chrome_removes_quotes_and_preamble() -> None:
    assert rd._strip_meta_chrome('"hello there"') == "hello there"
    assert rd._strip_meta_chrome("Reply: hello there") == "hello there"
    assert rd._strip_meta_chrome("Here is the reply: ok") == "ok"


# --------------------------------------------------------------------------- _call_gemini_json


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


def test_call_gemini_json_returns_none_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # _no_gemini autouse fixture already unsets GEMINI_API_KEY.
    assert rd._call_gemini_json("prompt") is None


def test_call_gemini_json_parses_valid_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    body = _candidate_text('{"engage": true, "comment": "hi", "reason": "good fit"}')
    monkeypatch.setattr(rd.httpx, "post", _fake_post(body))

    result = rd._call_gemini_json("prompt")

    assert result == {"engage": True, "comment": "hi", "reason": "good fit"}


def test_call_gemini_json_returns_none_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(rd.httpx, "post", _fake_post({}, status_code=500))

    assert rd._call_gemini_json("prompt") is None


def test_call_gemini_json_returns_none_on_malformed_json_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    body = _candidate_text("not actually json")
    monkeypatch.setattr(rd.httpx, "post", _fake_post(body))

    assert rd._call_gemini_json("prompt") is None


def test_call_gemini_json_returns_none_when_engage_field_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    body = _candidate_text('{"comment": "hi", "reason": "no engage field"}')
    monkeypatch.setattr(rd.httpx, "post", _fake_post(body))

    assert rd._call_gemini_json("prompt") is None


def test_call_gemini_json_returns_none_on_no_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(rd.httpx, "post", _fake_post({"candidates": []}))

    assert rd._call_gemini_json("prompt") is None
