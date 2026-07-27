"""Tests for the FB comment action's thin spec + draft delegation.

The drain/filter logic is covered in test_commenter.py; here we verify the FB
``CommenterSpec`` is wired correctly and ``_draft`` calls the short drafter with
the right post fields.
"""
# ruff: noqa: S101

from __future__ import annotations

import pytest
from scripts import fb_comment


def test_spec_is_facebook() -> None:
    spec = fb_comment.SPEC
    assert spec.platform == "facebook"
    assert spec.skill_name == "fb-comment"
    assert spec.target_field == "group_name"
    assert spec.guard_key == "comment_composer_facebook"
    assert spec.post_fn is not None


def test_draft_delegates_post_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake(**kwargs: object) -> str:
        captured.update(kwargs)
        return "drafted"

    monkeypatch.setattr(fb_comment, "draft_short_comment_for_post", _fake)
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
