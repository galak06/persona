"""Tests for reply_follower.py — the non-browser bits.

Playwright scraping + posting is covered by manual smoke runs, not these tests —
DOM behavior on real FB would make them flaky and slow.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

import reply_follower


def _item(post_id: str, posted_at: str, platform: str = "facebook", status: str = "posted") -> dict:
    return {
        "post_id": post_id,
        "posted_at": posted_at,
        "platform": platform,
        "status": status,
        "comment_text": "x" * 80,
    }


def test_recent_posted_fb_comments_respects_days_window() -> None:
    now = datetime.now(UTC)
    queue = [
        _item("recent", (now - timedelta(days=1)).isoformat()),
        _item("old", (now - timedelta(days=10)).isoformat()),
    ]
    recent = reply_follower.recent_posted_fb_comments(queue, days=7)
    assert [i["post_id"] for i in recent] == ["recent"]


def test_recent_posted_fb_comments_filters_non_fb_and_non_posted() -> None:
    now = datetime.now(UTC)
    ts = (now - timedelta(hours=1)).isoformat()
    queue = [
        _item("fb_posted", ts),
        _item("fb_pending", ts, status="pending"),
        _item("ig_posted", ts, platform="instagram"),
    ]
    recent = reply_follower.recent_posted_fb_comments(queue, days=7)
    assert [i["post_id"] for i in recent] == ["fb_posted"]


def test_recent_posted_fb_comments_handles_missing_posted_at() -> None:
    queue = [{"post_id": "x", "platform": "facebook", "status": "posted"}]
    assert reply_follower.recent_posted_fb_comments(queue, days=7) == []


def test_recent_posted_fb_comments_accepts_z_suffix() -> None:
    now = datetime.now(UTC)
    queue = [_item("z", (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z"))]
    recent = reply_follower.recent_posted_fb_comments(queue, days=7)
    assert len(recent) == 1


def test_draft_reply_is_voice_valid(monkeypatch) -> None:
    # Force the fallback template path so the test is deterministic
    # (no network, no GEMINI_API_KEY dependency).
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    from comment_generator import validate_voice

    text = reply_follower.draft_reply(
        their_text="How long before we saw results?",
        their_author="Sarah M.",
    )
    valid, violations = validate_voice(text)
    assert valid, f"draft voice failed: {violations}"
    assert "Sarah" in text
    assert text.rstrip().endswith("?"), "reply should end with a question"


def test_draft_reply_handles_empty_author(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    text = reply_follower.draft_reply(their_text="?", their_author="")
    assert "there" in text or text
    assert len(text) > 20


def test_load_save_json_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "x.json"
    reply_follower.save_json(p, {"a": 1, "b": [2, 3]})
    assert reply_follower.load_json(p, {}) == {"a": 1, "b": [2, 3]}


def test_load_json_returns_default_when_missing(tmp_path: Path) -> None:
    assert reply_follower.load_json(tmp_path / "nope.json", {"default": True}) == {
        "default": True
    }
