"""Tests for the IG scanner's skill-bound drafter wiring (SKILL.md path).

Mirrors ``test_ig_comment.py``: the single-pass scan (``scripts/ig_engager.py``)
now drafts through ``draft_helper.for_skill("ig-engager")`` instead of the
legacy module-level path. Importing ``scripts.ig_engager`` exercises the
module-level binding against the REAL ``.claude/skills/ig-engager/SKILL.md``
and the ``$BRAND_DIR`` (ci_brand) config, so these tests double as an
integration check that the ``## LLM Prompt`` section loads and renders. The
payload test captures the ``httpx.post`` body and asserts the voice rules +
rendered brand values arrive as ``systemInstruction`` (behavioural drain-loop
coverage lives in ``tests/lib/engagement/test_ig_engager_with_fake.py``).
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from scripts import ig_engager

from lib import draft_helper

# Passes lib.comment_generator.validate_voice (same string test_skill_drafter uses).
_VALID = (
    "We switched Nalla to a similar topper last week and her coat looks "
    "great — how long did your transition take?"
)


def test_drafter_is_bound_to_ig_engager_skill() -> None:
    """The module-level binder loads the ig-engager SKILL.md eagerly, so a
    broken skill file aborts at import — before any hashtag is scanned."""
    assert isinstance(ig_engager._DRAFTER, draft_helper.SkillDrafter)
    assert ig_engager._DRAFTER.skill == "ig-engager"


class _FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


def test_drafter_sends_skill_system_instruction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Full-stack payload capture through the bound drafter: the rendered
    ig-engager SKILL.md section (voice rules + ci_brand values) is sent as
    ``systemInstruction``; the user prompt carries only per-post context."""
    seen: dict[str, Any] = {}

    def _post(*_args: object, **kwargs: Any) -> _FakeResponse:
        seen["payload"] = kwargs.get("json")
        text = json.dumps({"engage": True, "comment": _VALID, "reason": "good fit"})
        return _FakeResponse({"candidates": [{"content": {"parts": [{"text": text}]}}]})

    monkeypatch.setattr(httpx, "post", _post)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)

    out = ig_engager._DRAFTER.draft_comment_for_post(
        platform="instagram",
        post_text="Anyone tried a topper?",
        group_or_hashtag="#dogfood",
        post_url="https://www.instagram.com/p/x/",
    )

    assert out == _VALID
    payload = seen["payload"]
    system_text = payload["systemInstruction"]["parts"][0]["text"]
    # A distinctive BRAND VOICE phrase from the SKILL.md reached the SYSTEM prompt.
    assert "Warm, specific, slightly analytical; not salesy, not clinical" in system_text
    # {{brand.*}} placeholders rendered from the ci_brand fixture config.
    assert (
        "You are Your Persona Name, the voice behind Your Brand Name (yourbrand.com)" in system_text
    )
    assert "Your Mascot is part of the brand's story" in system_text
    assert "{{brand." not in system_text
    # The user prompt carries only per-post context — no leaked voice rules.
    user_text = payload["contents"][0]["parts"][0]["text"]
    assert "Anyone tried a topper?" in user_text
    assert "BRAND VOICE" not in user_text
