"""Tests for the IG comment action's thin spec + draft delegation.

Mirrors test_fb_comment.py — the shared drain/filter logic is in
test_commenter.py; here we verify the IG ``CommenterSpec`` wiring and that
``_draft`` calls the standard drafter with the hashtag + post fields.
"""
# ruff: noqa: S101

from __future__ import annotations

import pytest
from scripts import ig_comment


def test_spec_is_instagram() -> None:
    spec = ig_comment.SPEC
    assert spec.platform == "instagram"
    assert spec.skill_name == "ig-comment"
    assert spec.target_field == "hashtag"
    assert spec.guard_key == "comment_composer_instagram"
    assert "accounts/login" in spec.login_markers


def test_draft_delegates_post_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake(**kwargs: object) -> str:
        captured.update(kwargs)
        return "drafted"

    monkeypatch.setattr(ig_comment, "draft_comment_for_post", _fake)
    item = {
        "post_text": "What kibble do you feed a senior pup?",
        "hashtag": "doghealth",
        "post_url": "https://www.instagram.com/p/abc/",
    }
    out = ig_comment._draft(item)

    assert out == "drafted"
    assert captured["platform"] == "instagram"
    assert captured["post_text"] == "What kibble do you feed a senior pup?"
    assert captured["group_or_hashtag"] == "doghealth"
    assert captured["post_url"] == "https://www.instagram.com/p/abc/"
