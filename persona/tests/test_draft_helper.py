"""Tests for the agentic drafting helper used by fb_comment/ig_comment.

Mocks ``draft_helper._call_gemini_json`` (no network) and exercises the real
``lib.comment_generator.validate_voice`` so the voice contract is enforced.
The agent's own engage/decline decision is the approval gate for outbound
comments — ``engage: false`` must return ``""`` exactly like every other
failure path, without ever reaching voice validation.
"""

from __future__ import annotations

import logging

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


def _engaged(comment: str, reason: str = "good fit") -> dict:
    return {"engage": True, "comment": comment, "reason": reason}


def _declined(reason: str = "generic post, no real angle") -> dict:
    return {"engage": False, "comment": "", "reason": reason}


def test_short_draft_returns_validated_text(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch)
    monkeypatch.setattr(draft_helper, "_call_gemini_json", lambda *a, **k: _engaged(_VALID_SHORT))

    out = draft_helper.draft_short_comment_for_post(
        platform="facebook", post_text="Anyone tried a new topper?", group_or_hashtag="Dogs"
    )
    assert out == _VALID_SHORT


def test_short_draft_prompt_asks_for_one_sentence(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch)
    seen: dict[str, str] = {}

    def _capture(prompt: str, **_: object) -> dict:
        seen["prompt"] = prompt
        return _engaged(_VALID_SHORT)

    monkeypatch.setattr(draft_helper, "_call_gemini_json", _capture)
    draft_helper.draft_short_comment_for_post(
        platform="facebook", post_text="post body here", group_or_hashtag="Dogs"
    )
    assert "ONE short sentence (15-25 words)" in seen["prompt"]
    assert "post body here" in seen["prompt"]  # grounded in THIS post
    assert '"engage"' in seen["prompt"]  # asks for the structured decision
    assert (
        "12 words max" in seen["prompt"]
    )  # reason is length-capped so it can't eat the token budget


def test_token_budgets_leave_headroom_for_json_envelope() -> None:
    """The engage/comment/reason JSON envelope shares maxOutputTokens with the
    comment, so the budgets must leave room for the reason field + braces —
    otherwise a slightly-long reply truncates to unparseable JSON and the post
    is silently dropped. Regression guard against shrinking them back."""
    assert draft_helper._SHORT_MAX_TOKENS >= 300
    assert draft_helper._MAX_TOKENS >= 600


def test_short_draft_retries_once_on_voice_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch)
    calls = {"n": 0}

    def _two_step(*_a: object, **_k: object) -> dict:
        calls["n"] += 1
        return _engaged(_INVALID) if calls["n"] == 1 else _engaged(_VALID_SHORT)

    monkeypatch.setattr(draft_helper, "_call_gemini_json", _two_step)
    out = draft_helper.draft_short_comment_for_post(
        platform="facebook", post_text="x", group_or_hashtag="Dogs"
    )
    assert out == _VALID_SHORT
    assert calls["n"] == 2


def test_short_draft_empty_after_two_voice_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch)
    monkeypatch.setattr(draft_helper, "_call_gemini_json", lambda *a, **k: _engaged(_INVALID))
    out = draft_helper.draft_short_comment_for_post(
        platform="facebook", post_text="x", group_or_hashtag="Dogs"
    )
    assert out == ""


def test_short_draft_missing_key_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing key degrades to a per-item skip ("") like any other upstream
    failure — it must NOT raise and abort the whole batch. Uses the real
    _call_gemini_json (unmocked), which returns None when the key is absent."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    out = draft_helper.draft_short_comment_for_post(
        platform="facebook", post_text="x", group_or_hashtag="Dogs"
    )
    assert out == ""


def test_short_draft_fails_closed_on_non_boolean_engage(monkeypatch: pytest.MonkeyPatch) -> None:
    """engage is read fail-closed: a truthy non-True value (e.g. the JSON
    string "false" from a schema hiccup) must be treated as a decline and
    never posted, even though the comment field is populated."""
    _set_key(monkeypatch)
    monkeypatch.setattr(
        draft_helper,
        "_call_gemini_json",
        lambda *a, **k: {"engage": "false", "comment": _VALID_SHORT, "reason": "x"},
    )
    out = draft_helper.draft_short_comment_for_post(
        platform="facebook", post_text="x", group_or_hashtag="Dogs"
    )
    assert out == ""


def test_short_draft_strips_meta_chrome_before_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leading preamble / wrapping quotes in the comment value are stripped
    before voice validation, so chrome can't slip an off-brand opener past
    validate_voice's startswith-based generic-opener guard."""
    _set_key(monkeypatch)
    monkeypatch.setattr(
        draft_helper, "_call_gemini_json", lambda *a, **k: _engaged(f'"{_VALID_SHORT}"')
    )
    out = draft_helper.draft_short_comment_for_post(
        platform="facebook", post_text="x", group_or_hashtag="Dogs"
    )
    assert out == _VALID_SHORT  # wrapping quotes stripped, then validated


def test_short_draft_logs_when_engaged_but_blank(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """engage:true with a blank comment is an attributable drop, not a silent
    one — it must emit a structured warning so the skip is traceable."""
    _set_key(monkeypatch)
    monkeypatch.setattr(draft_helper, "_call_gemini_json", lambda *a, **k: _engaged(""))
    with caplog.at_level(logging.WARNING, logger=draft_helper.log.name):
        out = draft_helper.draft_short_comment_for_post(
            platform="facebook", post_text="x", group_or_hashtag="Dogs"
        )
    assert out == ""
    assert any("draft_engaged_but_blank" in str(r.msg) for r in caplog.records)


# --------------------------------------------------------------------------- agent decline


def test_short_draft_returns_empty_when_agent_declines(monkeypatch: pytest.MonkeyPatch) -> None:
    """engage: false is the agent's own approval decision -- never reaches
    voice validation, just like every other skip path."""
    _set_key(monkeypatch)
    monkeypatch.setattr(draft_helper, "_call_gemini_json", lambda *a, **k: _declined())

    out = draft_helper.draft_short_comment_for_post(
        platform="facebook", post_text="generic low-effort post", group_or_hashtag="Dogs"
    )
    assert out == ""


def test_short_draft_declines_on_retry_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """First attempt engages but fails voice; the retry itself declines."""
    _set_key(monkeypatch)
    calls = {"n": 0}

    def _two_step(*_a: object, **_k: object) -> dict:
        calls["n"] += 1
        return _engaged(_INVALID) if calls["n"] == 1 else _declined("not worth forcing a rewrite")

    monkeypatch.setattr(draft_helper, "_call_gemini_json", _two_step)
    out = draft_helper.draft_short_comment_for_post(
        platform="facebook", post_text="x", group_or_hashtag="Dogs"
    )
    assert out == ""
    assert calls["n"] == 2


def test_short_draft_empty_when_gemini_call_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch)
    monkeypatch.setattr(draft_helper, "_call_gemini_json", lambda *a, **k: None)

    out = draft_helper.draft_short_comment_for_post(
        platform="facebook", post_text="x", group_or_hashtag="Dogs"
    )
    assert out == ""


def test_short_draft_empty_when_engaged_but_comment_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed engage:true response with no real comment text must not
    crash voice validation on an empty string -- treated as a skip."""
    _set_key(monkeypatch)
    monkeypatch.setattr(draft_helper, "_call_gemini_json", lambda *a, **k: _engaged(""))

    out = draft_helper.draft_short_comment_for_post(
        platform="facebook", post_text="x", group_or_hashtag="Dogs"
    )
    assert out == ""


# --------------------------------------------------------------------------- long path (IG)


def test_long_draft_returns_validated_text(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch)
    monkeypatch.setattr(draft_helper, "_call_gemini_json", lambda *a, **k: _engaged(_VALID_SHORT))

    out = draft_helper.draft_comment_for_post(
        platform="instagram", post_text="Anyone tried a new topper?", group_or_hashtag="#dogfood"
    )
    assert out == _VALID_SHORT


def test_long_draft_returns_empty_when_agent_declines(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch)
    monkeypatch.setattr(draft_helper, "_call_gemini_json", lambda *a, **k: _declined())

    out = draft_helper.draft_comment_for_post(
        platform="instagram", post_text="generic low-effort post", group_or_hashtag="#dogfood"
    )
    assert out == ""
