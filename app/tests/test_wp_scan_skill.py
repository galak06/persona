"""Tests for wp_scan's skill-bound drafter (wp-comment-handler SKILL.md).

Companion to test_wp_comments.py (which owns the spam-heuristic + poster
coverage). Importing ``wp_scan`` exercises the module-level
``for_skill("wp-comment-handler")`` binding against the real SKILL.md +
$BRAND_DIR; the payload test captures the ``httpx.post`` boundary to prove
the system/user split — voice rules travel as ``systemInstruction`` while
per-comment context and ``site_context`` stay in the USER prompt.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import wp_scan
from lib import draft_helper

# Passes lib.comment_generator.validate_voice (same string test_skill_drafter uses).
_VALID = (
    "We switched Nalla to a similar topper last week and her coat looks "
    "great — how long did your transition take?"
)


def test_drafter_is_bound_to_wp_comment_handler_skill() -> None:
    """The module-level binder loads the wp-comment-handler SKILL.md eagerly,
    so a broken skill file aborts at import — before any comment is fetched."""
    assert isinstance(wp_scan._DRAFTER, draft_helper.SkillDrafter)
    assert wp_scan._DRAFTER.skill == "wp-comment-handler"


class _FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


def test_payload_splits_system_rules_from_site_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """systemInstruction carries the SKILL.md voice rules; the per-comment
    context (including site_context) stays in the USER part."""
    seen: dict[str, Any] = {}

    def _post(*_args: object, **kwargs: Any) -> _FakeResponse:
        seen["payload"] = kwargs.get("json")
        text = json.dumps({"engage": True, "comment": _VALID, "reason": "good fit"})
        return _FakeResponse({"candidates": [{"content": {"parts": [{"text": text}]}}]})

    monkeypatch.setattr(httpx, "post", _post)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)

    site_context = "Recent post: Pumpkin Biscuits for Sensitive Tummies"
    out = wp_scan._DRAFTER.draft_comment_for_post(
        platform="wordpress",
        post_text="Is this recipe okay for a senior beagle?",
        group_or_hashtag=None,
        post_url="https://yourbrand.com/pumpkin-biscuits/#comment-9",
        site_context=site_context,
    )

    assert out == _VALID
    payload = seen["payload"]
    system_text = payload["systemInstruction"]["parts"][0]["text"]
    # Distinctive voice-rules phrase from the SKILL.md '## LLM Prompt' section.
    assert "Warm, specific, slightly analytical" in system_text
    assert site_context not in system_text
    user_text = payload["contents"][0]["parts"][0]["text"]
    assert site_context in user_text
    assert "PLATFORM: wordpress" in user_text
    assert "Is this recipe okay for a senior beagle?" in user_text
