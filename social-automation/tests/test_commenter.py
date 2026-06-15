# pyright: reportMissingImports=false
"""Tests for the shared engagement-commenter core (lib.engagement.commenter).

Covers the platform-agnostic ``_pending_items`` filter + dedup stamping against
a synthetic spec — the FB and IG commenters reuse this verbatim.
"""
# ruff: noqa: S101

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_LIB = Path(__file__).resolve().parent.parent / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import deduplication
from lib.engagement.commenter import CommenterSpec, _pending_items


def _spec(platform: str = "facebook", target_field: str = "group_name") -> CommenterSpec:
    return CommenterSpec(
        platform=platform,
        skill_name=f"{platform}-comment",
        label="X",
        guard_key="k",
        session_file=Path("/tmp/s"),
        queue_file=Path("/tmp/q"),
        last_run_file=Path("/tmp/lr"),
        log_file=Path("/tmp/log"),
        home_url="https://example.test",
        login_markers=("login",),
        target_field=target_field,
        draft_fn=lambda _item: "drafted",
        post_fn=lambda *_a: True,
        session_missing_msg="no session",
    )


def _item(post_id: str, *, platform: str = "facebook", status: str = "pending") -> dict:
    return {
        "platform": platform,
        "post_id": post_id,
        "status": status,
        "post_url": f"https://x/{post_id}",
        "post_text": "Anyone tried a fresh-food topper?",
        "group_name": "Dogs",
    }


def test_pending_filters_platform_and_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deduplication, "already_commented", lambda *_a, **_k: False)
    queue = [
        _item("p1"),
        _item("p2", status="posted"),
        _item("p3", platform="instagram"),
        _item("p4"),
    ]
    assert [i["post_id"] for i in _pending_items(_spec(), queue)] == ["p1", "p4"]


def test_pending_respects_platform_of_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deduplication, "already_commented", lambda *_a, **_k: False)
    queue = [_item("a", platform="facebook"), _item("b", platform="instagram")]
    ig = _pending_items(_spec(platform="instagram", target_field="hashtag"), queue)
    assert [i["post_id"] for i in ig] == ["b"]


def test_pending_skips_only_already_commented(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deduplication, "already_commented", lambda _p, pid: pid == "done")
    queue = [_item("done"), _item("fresh")]
    pending = _pending_items(_spec(), queue)

    assert [i["post_id"] for i in pending] == ["fresh"]
    assert queue[0]["status"] == "already_commented"
    assert "_blocked_reason" in queue[0]
