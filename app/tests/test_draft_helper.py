"""Tests for the agentic drafting helper used by fb_engager/ig_engager/ig_comment.

Mocks ``draft_helper._llm_json`` (no network) and exercises the real
``lib.comment_generator.validate_voice`` so the voice contract is enforced.
The agent's own engage/decline decision is the approval gate for outbound
comments — ``engage: false`` must return ``""`` exactly like every other
failure path, without ever reaching voice validation.

Every draft goes through a ``SkillDrafter`` (the only drafting path). These
tests stub the SKILL.md system prompt so they cover the engage/validate/retry
mechanics only; real skill loading is covered by test_skill_drafter.py and
the rendered prompts themselves by test_skill_prompts.py.
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

_SYSTEM = "SYSTEM RULES (stand-in for the bound skill's ## LLM Prompt section)."


@pytest.fixture
def drafter(monkeypatch: pytest.MonkeyPatch) -> draft_helper.SkillDrafter:
    """A drafter with a stubbed system prompt — no SKILL.md / BRAND_DIR read."""
    monkeypatch.setattr(draft_helper, "_system_prompt", lambda _skill: _SYSTEM)
    return draft_helper.for_skill("fb-engager")


def _set_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")


def _engaged(comment: str, reason: str = "good fit") -> dict:
    return {"engage": True, "comment": comment, "reason": reason}


def _declined(reason: str = "generic post, no real angle") -> dict:
    return {"engage": False, "comment": "", "reason": reason}


def test_short_draft_returns_validated_text(
    monkeypatch: pytest.MonkeyPatch, drafter: draft_helper.SkillDrafter
) -> None:
    _set_key(monkeypatch)
    monkeypatch.setattr(draft_helper, "_llm_json", lambda *a, **k: _engaged(_VALID_SHORT))

    out = drafter.draft_short_comment_for_post(
        platform="facebook", post_text="Anyone tried a new topper?", group_or_hashtag="Dogs"
    )
    assert out == _VALID_SHORT


def test_user_prompt_carries_context_not_the_standing_rules(
    monkeypatch: pytest.MonkeyPatch, drafter: draft_helper.SkillDrafter
) -> None:
    """The USER prompt is per-post only. Voice rules, the engage-decision
    bullets and the length rule all live in the skill's SYSTEM prompt — a
    restatement here would be two sources of truth for the same rule."""
    _set_key(monkeypatch)
    seen: dict[str, object] = {}

    def _capture(prompt: str, **kwargs: object) -> dict:
        seen["prompt"] = prompt
        seen["system"] = kwargs.get("system")
        return _engaged(_VALID_SHORT)

    monkeypatch.setattr(draft_helper, "_llm_json", _capture)
    drafter.draft_short_comment_for_post(
        platform="facebook", post_text="post body here", group_or_hashtag="Dogs"
    )
    prompt = str(seen["prompt"])
    assert "post body here" in prompt
    assert '"engage"' in prompt
    assert "12 words max" in prompt
    assert "ONE short sentence" not in prompt
    assert "BRAND VOICE" not in prompt
    assert seen["system"] == _SYSTEM


def test_token_budgets_leave_headroom_for_json_envelope() -> None:
    """The engage/comment/reason JSON envelope shares maxOutputTokens with the
    comment, so the budgets must leave room for the reason field + braces —
    otherwise a slightly-long reply truncates to unparseable JSON and the post
    is silently dropped. Regression guard against shrinking them back."""
    assert draft_helper._SHORT_MAX_TOKENS >= 300
    assert draft_helper._MAX_TOKENS >= 600


def test_short_draft_retries_once_on_voice_failure(
    monkeypatch: pytest.MonkeyPatch, drafter: draft_helper.SkillDrafter
) -> None:
    _set_key(monkeypatch)
    calls = {"n": 0}

    def _two_step(*_a: object, **_k: object) -> dict:
        calls["n"] += 1
        return _engaged(_INVALID) if calls["n"] == 1 else _engaged(_VALID_SHORT)

    monkeypatch.setattr(draft_helper, "_llm_json", _two_step)
    out = drafter.draft_short_comment_for_post(
        platform="facebook", post_text="x", group_or_hashtag="Dogs"
    )
    assert out == _VALID_SHORT
    assert calls["n"] == 2


def test_short_draft_empty_after_two_voice_failures(
    monkeypatch: pytest.MonkeyPatch, drafter: draft_helper.SkillDrafter
) -> None:
    _set_key(monkeypatch)
    monkeypatch.setattr(draft_helper, "_llm_json", lambda *a, **k: _engaged(_INVALID))
    out = drafter.draft_short_comment_for_post(
        platform="facebook", post_text="x", group_or_hashtag="Dogs"
    )
    assert out == ""


def test_short_draft_missing_key_returns_empty(
    monkeypatch: pytest.MonkeyPatch, drafter: draft_helper.SkillDrafter
) -> None:
    """A missing key degrades to a per-item skip ("") like any other upstream
    failure — it must NOT raise and abort the whole batch. Uses the real
    _llm_json (unmocked), which returns None when the key is absent. Clears
    every provider key so none can silently take over: keyless means keyless.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOICE_PROVIDER", raising=False)
    out = drafter.draft_short_comment_for_post(
        platform="facebook", post_text="x", group_or_hashtag="Dogs"
    )
    assert out == ""


def test_short_draft_fails_closed_on_non_boolean_engage(
    monkeypatch: pytest.MonkeyPatch, drafter: draft_helper.SkillDrafter
) -> None:
    """engage is read fail-closed: a truthy non-True value (e.g. the JSON
    string "false" from a schema hiccup) must be treated as a decline and
    never posted, even though the comment field is populated."""
    _set_key(monkeypatch)
    monkeypatch.setattr(
        draft_helper,
        "_llm_json",
        lambda *a, **k: {"engage": "false", "comment": _VALID_SHORT, "reason": "x"},
    )
    out = drafter.draft_short_comment_for_post(
        platform="facebook", post_text="x", group_or_hashtag="Dogs"
    )
    assert out == ""


def test_short_draft_strips_meta_chrome_before_validation(
    monkeypatch: pytest.MonkeyPatch, drafter: draft_helper.SkillDrafter
) -> None:
    """Leading preamble / wrapping quotes in the comment value are stripped
    before voice validation, so chrome can't slip an off-brand opener past
    validate_voice's startswith-based generic-opener guard."""
    _set_key(monkeypatch)
    monkeypatch.setattr(draft_helper, "_llm_json", lambda *a, **k: _engaged(f'"{_VALID_SHORT}"'))
    out = drafter.draft_short_comment_for_post(
        platform="facebook", post_text="x", group_or_hashtag="Dogs"
    )
    assert out == _VALID_SHORT  # wrapping quotes stripped, then validated


def test_short_draft_logs_when_engaged_but_blank(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    drafter: draft_helper.SkillDrafter,
) -> None:
    """engage:true with a blank comment is an attributable drop, not a silent
    one — it must emit a structured warning so the skip is traceable."""
    _set_key(monkeypatch)
    monkeypatch.setattr(draft_helper, "_llm_json", lambda *a, **k: _engaged(""))
    with caplog.at_level(logging.WARNING, logger=draft_helper.log.name):
        out = drafter.draft_short_comment_for_post(
            platform="facebook", post_text="x", group_or_hashtag="Dogs"
        )
    assert out == ""
    assert any("draft_engaged_but_blank" in str(r.msg) for r in caplog.records)


# --------------------------------------------------------------------------- agent decline


def test_short_draft_returns_empty_when_agent_declines(
    monkeypatch: pytest.MonkeyPatch, drafter: draft_helper.SkillDrafter
) -> None:
    """engage: false is the agent's own approval decision -- never reaches
    voice validation, just like every other skip path."""
    _set_key(monkeypatch)
    monkeypatch.setattr(draft_helper, "_llm_json", lambda *a, **k: _declined())

    out = drafter.draft_short_comment_for_post(
        platform="facebook", post_text="generic low-effort post", group_or_hashtag="Dogs"
    )
    assert out == ""


def test_short_draft_declines_on_retry_too(
    monkeypatch: pytest.MonkeyPatch, drafter: draft_helper.SkillDrafter
) -> None:
    """First attempt engages but fails voice; the retry itself declines."""
    _set_key(monkeypatch)
    calls = {"n": 0}

    def _two_step(*_a: object, **_k: object) -> dict:
        calls["n"] += 1
        return _engaged(_INVALID) if calls["n"] == 1 else _declined("not worth forcing a rewrite")

    monkeypatch.setattr(draft_helper, "_llm_json", _two_step)
    out = drafter.draft_short_comment_for_post(
        platform="facebook", post_text="x", group_or_hashtag="Dogs"
    )
    assert out == ""
    assert calls["n"] == 2


def test_short_draft_empty_when_gemini_call_returns_none(
    monkeypatch: pytest.MonkeyPatch, drafter: draft_helper.SkillDrafter
) -> None:
    _set_key(monkeypatch)
    monkeypatch.setattr(draft_helper, "_llm_json", lambda *a, **k: None)

    out = drafter.draft_short_comment_for_post(
        platform="facebook", post_text="x", group_or_hashtag="Dogs"
    )
    assert out == ""


def test_short_draft_empty_when_engaged_but_comment_blank(
    monkeypatch: pytest.MonkeyPatch, drafter: draft_helper.SkillDrafter
) -> None:
    """A malformed engage:true response with no real comment text must not
    crash voice validation on an empty string -- treated as a skip."""
    _set_key(monkeypatch)
    monkeypatch.setattr(draft_helper, "_llm_json", lambda *a, **k: _engaged(""))

    out = drafter.draft_short_comment_for_post(
        platform="facebook", post_text="x", group_or_hashtag="Dogs"
    )
    assert out == ""


# --------------------------------------------------------------------------- long path (IG)


def test_long_draft_returns_validated_text(
    monkeypatch: pytest.MonkeyPatch, drafter: draft_helper.SkillDrafter
) -> None:
    _set_key(monkeypatch)
    monkeypatch.setattr(draft_helper, "_llm_json", lambda *a, **k: _engaged(_VALID_SHORT))

    out = drafter.draft_comment_for_post(
        platform="instagram", post_text="Anyone tried a new topper?", group_or_hashtag="#dogfood"
    )
    assert out == _VALID_SHORT


def test_long_draft_returns_empty_when_agent_declines(
    monkeypatch: pytest.MonkeyPatch, drafter: draft_helper.SkillDrafter
) -> None:
    _set_key(monkeypatch)
    monkeypatch.setattr(draft_helper, "_llm_json", lambda *a, **k: _declined())

    out = drafter.draft_comment_for_post(
        platform="instagram", post_text="generic low-effort post", group_or_hashtag="#dogfood"
    )
    assert out == ""


def _drafter_returning(
    monkeypatch: pytest.MonkeyPatch, payload: object
) -> draft_helper.SkillDrafter:
    """A SkillDrafter whose LLM call returns `payload`, with a stub system prompt."""
    monkeypatch.setattr(draft_helper, "_system_prompt", lambda _skill: "SYSTEM")
    monkeypatch.setattr(draft_helper, "_llm_json", lambda *_a, **_k: payload)
    return draft_helper.for_skill("ig-engager")


def test_last_outcome_distinguishes_upstream_failure_from_decline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead model must not look like the agent choosing not to engage."""
    d = _drafter_returning(monkeypatch, None)
    assert (
        d.draft_comment_for_post(
            platform="instagram", post_text="p", group_or_hashtag="h", post_url="u"
        )
        == ""
    )
    assert d.last_outcome == draft_helper.DRAFT_FAILED


def test_last_outcome_marks_a_genuine_agent_decline(monkeypatch: pytest.MonkeyPatch) -> None:
    d = _drafter_returning(monkeypatch, {"engage": False, "reason": "off topic"})
    assert (
        d.draft_comment_for_post(
            platform="instagram", post_text="p", group_or_hashtag="h", post_url="u"
        )
        == ""
    )
    assert d.last_outcome == draft_helper.AGENT_DECLINED


def test_last_outcome_marks_engaged_but_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    d = _drafter_returning(monkeypatch, {"engage": True, "comment": "", "reason": "r"})
    assert (
        d.draft_comment_for_post(
            platform="instagram", post_text="p", group_or_hashtag="h", post_url="u"
        )
        == ""
    )
    assert d.last_outcome == draft_helper.DRAFT_BLANK


def test_last_outcome_is_none_after_a_successful_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    good = "We tried something similar with our dog and it worked well — how did yours react?"
    d = _drafter_returning(monkeypatch, {"engage": True, "comment": good, "reason": "fit"})
    monkeypatch.setattr(draft_helper, "validate_voice", lambda *_a, **_k: (True, []))
    assert (
        d.draft_comment_for_post(
            platform="instagram", post_text="p", group_or_hashtag="h", post_url="u"
        )
        == good
    )
    assert d.last_outcome is None
