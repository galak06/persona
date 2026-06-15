"""Tests for the short, post-grounded draft variant used by fb_comment.

Mocks ``draft_helper._call_gemini`` (no network) and exercises the real
``lib.comment_generator.validate_voice`` so the voice contract is enforced for
the one-sentence FB comment path: trailing question, specificity, first-person.
"""
from __future__ import annotations

import pytest

import draft_helper

# A reply that satisfies validate_voice: has a "?", >=40 chars, mentions Nalla,
# a timeframe ("week"), and first-person ("we"); no medical/salesy/generic opener.
_VALID_SHORT = (
    "We switched Nalla to a similar topper last week and her coat looks "
    "great — how long did your transition take?"
)
# Fails voice: generic opener, no question, no specificity.
_INVALID = "great post!"


def _set_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")


def test_short_draft_returns_validated_text(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch)
    monkeypatch.setattr(draft_helper, "_call_gemini", lambda *a, **k: _VALID_SHORT)

    out = draft_helper.draft_short_comment_for_post(
        platform="facebook", post_text="Anyone tried a new topper?", group_or_hashtag="Dogs"
    )
    assert out == _VALID_SHORT


def test_short_draft_prompt_asks_for_one_sentence(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch)
    seen: dict[str, str] = {}

    def _capture(prompt: str, **_: object) -> str:
        seen["prompt"] = prompt
        return _VALID_SHORT

    monkeypatch.setattr(draft_helper, "_call_gemini", _capture)
    draft_helper.draft_short_comment_for_post(
        platform="facebook", post_text="post body here", group_or_hashtag="Dogs"
    )
    assert "ONE short sentence (15-25 words)" in seen["prompt"]
    assert "post body here" in seen["prompt"]  # grounded in THIS post


def test_short_draft_retries_once_on_voice_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch)
    calls = {"n": 0}

    def _two_step(*_a: object, **_k: object) -> str:
        calls["n"] += 1
        return _INVALID if calls["n"] == 1 else _VALID_SHORT

    monkeypatch.setattr(draft_helper, "_call_gemini", _two_step)
    out = draft_helper.draft_short_comment_for_post(
        platform="facebook", post_text="x", group_or_hashtag="Dogs"
    )
    assert out == _VALID_SHORT
    assert calls["n"] == 2


def test_short_draft_empty_after_two_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch)
    monkeypatch.setattr(draft_helper, "_call_gemini", lambda *a, **k: _INVALID)
    out = draft_helper.draft_short_comment_for_post(
        platform="facebook", post_text="x", group_or_hashtag="Dogs"
    )
    assert out == ""


def test_short_draft_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        draft_helper.draft_short_comment_for_post(
            platform="facebook", post_text="x", group_or_hashtag="Dogs"
        )
