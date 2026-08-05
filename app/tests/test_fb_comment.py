"""Tests for the FB comment action's thin spec + skill-bound draft delegation.

The drain/filter logic is covered in test_commenter.py; here we verify the FB
``CommenterSpec`` wiring and that ``_draft`` delegates to the skill-bound
drafter with the right post fields. Importing ``scripts.fb_comment`` also
exercises the module-level ``for_skill("fb-comment")`` binding against the
real SKILL.md + $BRAND_DIR, and the payload-capture test asserts the rendered
short-form rule travels as the systemInstruction with the brand mascot
substituted for the old hardcoded name.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from scripts import fb_comment

import draft_helper
from lib import skill_loader

# Passes lib.comment_generator.validate_voice (same string test_skill_drafter uses).
_VALID = (
    "We switched Nalla to a similar topper last week and her coat looks "
    "great — how long did your transition take?"
)


def test_spec_is_facebook() -> None:
    spec = fb_comment.SPEC
    assert spec.platform == "facebook"
    assert spec.skill_name == "fb-comment"
    assert spec.target_field == "group_name"
    assert spec.guard_key == "comment_composer_facebook"
    assert spec.post_fn is not None


def test_drafter_is_bound_to_fb_comment_skill() -> None:
    """The module-level binder loads the fb-comment SKILL.md eagerly, so a
    broken skill file aborts at import — before any queue item is touched."""
    assert isinstance(fb_comment._DRAFTER, draft_helper.SkillDrafter)
    assert fb_comment._DRAFTER.skill == "fb-comment"


def test_draft_delegates_post_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake(**kwargs: object) -> str:
        captured.update(kwargs)
        return "drafted"

    monkeypatch.setattr(fb_comment._DRAFTER, "draft_short_comment_for_post", _fake)
    item = {
        "post_text": "Anyone tried a fresh-food topper?",
        "group_name": "Dogs",
        "post_url": "https://www.facebook.com/groups/1/posts/p1",
    }
    out = fb_comment._draft(item)

    assert out == "drafted"
    assert captured["platform"] == "facebook"
    assert captured["post_text"] == "Anyone tried a fresh-food topper?"
    assert captured["group_or_hashtag"] == "Dogs"
    assert captured["post_url"] == "https://www.facebook.com/groups/1/posts/p1"


class _FakeResponse:
    status_code = 200
    text = ""

    def json(self) -> dict[str, Any]:
        text = json.dumps({"engage": True, "comment": _VALID, "reason": "good fit"})
        return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def test_draft_sends_rendered_short_rule_as_system_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full-stack payload capture against the REAL fb-comment SKILL.md and the
    test-run $BRAND_DIR (ci_brand): the systemInstruction must carry the
    short-form rule with ``{{brand.mascot}}`` rendered to the brand's mascot —
    not left as a placeholder, and not the old hardcoded "Nalla"."""
    seen: dict[str, Any] = {}

    def _post(*_args: object, **kwargs: Any) -> _FakeResponse:
        seen["payload"] = kwargs.get("json")
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", _post)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)

    out = fb_comment._draft(
        {
            "post_text": "Anyone tried a fresh-food topper?",
            "group_name": "Dogs",
            "post_url": "https://www.facebook.com/groups/1/posts/p1",
        }
    )

    assert out == _VALID
    payload = seen["payload"]
    system_text = payload["systemInstruction"]["parts"][0]["text"]
    brand = skill_loader.load_brand_vars()
    assert brand.mascot and brand.mascot != "Nalla"  # ci_brand precondition
    assert "ONE short sentence (15-25 words)" in system_text
    assert f"mention {brand.mascot} or our own experience" in system_text
    assert "{{brand." not in system_text
    assert "Nalla" not in system_text  # the parametrization IS the refactor's point
    user_text = payload["contents"][0]["parts"][0]["text"]
    assert "PLATFORM: facebook" in user_text
    assert "Anyone tried a fresh-food topper?" in user_text
    assert "Respond with ONLY a JSON object" in user_text  # envelope stays Python-side
