"""Tests for ``draft_helper.SkillDrafter`` — the SKILL.md system/user split.

Companion to test_draft_helper.py (kept separate for the 300-line file
budget; that file owns the engage/validate/retry mechanics with the system
prompt stubbed). Uses a tmp_path skill fixture via monkeypatched
``skill_loader.default_skills_dir``/``load_brand_vars`` (caches cleared around
each test) and captures payloads either at the ``httpx.post`` boundary
(full-stack: what lands in ``systemInstruction`` vs ``contents``) or at
``draft_helper._llm_json`` (retry mechanics).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from lib import draft_helper, skill_loader
from lib.skill_loader import BrandVars

# Passes lib.comment_generator.validate_voice (same string test_draft_helper uses).
_VALID = (
    "We switched Nalla to a similar topper last week and her coat looks "
    "great — how long did your transition take?"
)
_INVALID = "great post!"

# The length rule sits in the skill body (system prompt) — the assertions below
# pin it there and out of the user prompt.
_LENGTH_RULE = "the reply should be ONE short sentence (15-25 words)"
_SKILL_BODY = (
    "---\nname: test-skill\ndescription: fixture\n---\n\n"
    "# Test Skill\n\nchoreography (never sent)\n\n"
    "## LLM Prompt\n\n"
    "<!-- marker -->\n\n"
    "SYSTEM RULES for {{brand.persona}} of {{brand.name}}.\n"
    f"If you decide to engage, {_LENGTH_RULE} mentioning {{{{brand.mascot}}}}.\n"
)

_BRAND = BrandVars(
    name="Acme Dogs",
    name_lower="acme dogs",
    domain="acmedogs.example",
    mascot="Rex",
    persona="Rex's Human",
)


@pytest.fixture
def skill_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    skill_dir = tmp_path / "skills" / "test-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(_SKILL_BODY, encoding="utf-8")
    monkeypatch.setattr(skill_loader, "default_skills_dir", lambda: tmp_path / "skills")
    monkeypatch.setattr(skill_loader, "load_brand_vars", lambda brand_dir=None: _BRAND)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    # Pin the provider: the full-stack tests below capture at httpx.post, so an
    # ambient ANTHROPIC_API_KEY must not route lib.llm_client to the SDK.
    monkeypatch.setenv("VOICE_PROVIDER", "gemini")
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    skill_loader.load_skill_prompt.cache_clear()
    yield
    skill_loader.load_skill_prompt.cache_clear()


class _FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


def _engaged_body(comment: str) -> dict[str, Any]:
    text = json.dumps({"engage": True, "comment": comment, "reason": "good fit"})
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def _capture_post(seen: dict[str, Any]) -> Any:
    def _post(*_args: object, **kwargs: Any) -> _FakeResponse:
        seen["payload"] = kwargs.get("json")
        return _FakeResponse(_engaged_body(_VALID))

    return _post


# ----------------------------------------------------------- system/user split


def test_skill_drafter_sends_system_instruction(
    skill_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(httpx, "post", _capture_post(seen))
    monkeypatch.setattr(draft_helper, "_nalla_facts", lambda: "")

    drafter = draft_helper.for_skill("test-skill")
    out = drafter.draft_comment_for_post(
        platform="instagram",
        post_text="Anyone tried a topper?",
        group_or_hashtag="#dogfood",
        post_url="https://www.instagram.com/p/x/",
    )

    assert out == _VALID
    payload = seen["payload"]
    system_text = payload["systemInstruction"]["parts"][0]["text"]
    assert system_text.startswith("SYSTEM RULES for Rex's Human of Acme Dogs.")
    assert _LENGTH_RULE in system_text  # the length rule rides the SYSTEM prompt
    user_text = payload["contents"][0]["parts"][0]["text"]
    assert "Anyone tried a topper?" in user_text
    assert "PLATFORM: instagram" in user_text
    assert "Respond with ONLY a JSON object" in user_text  # JSON envelope stays Python-side
    assert "SYSTEM RULES" not in user_text  # skill text never leaks into the user prompt
    assert _LENGTH_RULE not in user_text  # ...and the length rule is never restated here


def test_system_prompt_appends_brand_facts(
    skill_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(draft_helper, "_nalla_facts", lambda: "Rex eats kibble.")
    drafter = draft_helper.for_skill("test-skill")
    assert drafter._system.startswith("SYSTEM RULES for Rex's Human of Acme Dogs.")
    assert "BRAND FACTS" in drafter._system
    assert drafter._system.endswith("Rex eats kibble.")


# ------------------------------------------------------------ retry mechanics


def test_retry_appends_violations_to_user_prompt_only(
    skill_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str | None]] = []

    def _fake_json(prompt: str, *, max_tokens: int, system: str | None = None) -> dict[str, Any]:
        calls.append((prompt, system))
        comment = _INVALID if len(calls) == 1 else _VALID
        return {"engage": True, "comment": comment, "reason": "r"}

    monkeypatch.setattr(draft_helper, "_llm_json", _fake_json)
    out = draft_helper.for_skill("test-skill").draft_comment_for_post(
        platform="instagram", post_text="x", group_or_hashtag=None
    )

    assert out == _VALID
    assert len(calls) == 2
    (prompt1, system1), (prompt2, system2) = calls
    assert system1 is not None and "SYSTEM RULES" in system1
    assert system2 == system1  # system prompt constant across attempts
    assert "failed brand-voice" not in prompt1
    assert prompt2.startswith(prompt1)  # retry suffix appended to the USER prompt
    assert "failed brand-voice" in prompt2


def test_skill_drafter_short_path_uses_short_budget_and_skill_length_rule(
    skill_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The short path differs from the long one only in token budget (and in
    sending no site context) — the requested LENGTH comes from the bound
    skill's own rule, never from a Python-side restatement."""
    seen: dict[str, Any] = {}

    def _fake_json(prompt: str, *, max_tokens: int, system: str | None = None) -> dict[str, Any]:
        seen.update(prompt=prompt, max_tokens=max_tokens, system=system)
        return {"engage": True, "comment": _VALID, "reason": "r"}

    monkeypatch.setattr(draft_helper, "_llm_json", _fake_json)
    out = draft_helper.for_skill("test-skill").draft_short_comment_for_post(
        platform="facebook", post_text="body here", group_or_hashtag="Dogs"
    )

    assert out == _VALID
    assert _LENGTH_RULE not in seen["prompt"]
    assert seen["max_tokens"] == draft_helper._SHORT_MAX_TOKENS
    assert seen["system"] is not None and _LENGTH_RULE in seen["system"]


# ------------------------------------------------------- decline + fail-fast


def test_skill_drafter_decline_returns_empty(
    skill_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        draft_helper,
        "_llm_json",
        lambda *a, **k: {"engage": False, "comment": "", "reason": "no angle"},
    )
    drafter = draft_helper.for_skill("test-skill")
    assert (
        drafter.draft_comment_for_post(platform="instagram", post_text="x", group_or_hashtag=None)
        == ""
    )


def test_for_skill_missing_skill_raises_at_bind_time(skill_env: None) -> None:
    """A broken/missing SKILL.md must surface at startup (binder init), not
    mid-drain on the first queue item."""
    with pytest.raises(skill_loader.SkillPromptError):
        draft_helper.for_skill("no-such-skill")
