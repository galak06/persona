"""End-to-end test of ``run_fb_scan()`` with FakeAdapter — no browser, no
network.

Drives the FB scanner through dedup, scoring, drafting, queue write, and
rate-limit gating with a canned FakeAdapter instead of Playwright.

Shared fixture (``fb_environment``) and the ``read_queue`` helper live in
``conftest.py``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.engagement.adapters.fake import FakeAdapter, FakeSource
from lib.engagement.post import Post

from .conftest import read_queue


def _group(group_id: str, name: str = "G") -> FakeSource:
    return FakeSource(
        id=group_id,
        name=name,
        url=f"https://www.facebook.com/groups/{group_id}",
    )


def _make_fb_post(
    post_id: str,
    text: str,
    source: FakeSource,
    *,
    category: str = "food",
    comment_count: int = 10,
) -> Post:
    return Post(
        platform="facebook",
        post_id=post_id,
        post_url=f"https://www.facebook.com/groups/{source.id}/posts/{post_id}",
        text=text,
        source_id=source.id,
        source_name=source.name,
        source_url=source.url,
        platform_extra={"category": category, "comment_count": comment_count},
    )


def test_fb_scan_queues_high_score_posts(fb_environment: dict[str, Path]) -> None:
    """High-relevance posts get queued; low-relevance posts don't."""
    group = _group("111", "Test Dog Group")
    adapter = FakeAdapter(
        "facebook",
        [group],
        {
            "111": [
                _make_fb_post(
                    "p1",
                    "What dog food kibble is best for my golden retriever's nutrition?",
                    group,
                ),
                _make_fb_post("p2", "Random unrelated content about cars", group),
            ]
        },
    )

    from scripts.fb_scan import run_fb_scan

    queued_count = run_fb_scan(adapter=adapter)
    queued_ids = [r["post_id"] for r in read_queue(fb_environment["queue_file"])]

    assert "p1" in queued_ids
    assert "p2" not in queued_ids
    assert queued_count == 1


def test_fb_scan_inline_queueing_multiple_posts(
    fb_environment: dict[str, Path],
) -> None:
    """FB queues every qualifying post inline (no cherry-pick at slice 2)."""
    group = _group("222")
    posts = [
        _make_fb_post(
            f"p{i}",
            "best dog food kibble nutrition recipe ingredients raw diet",
            group,
        )
        for i in range(3)
    ]
    adapter = FakeAdapter("facebook", [group], {"222": posts})

    from scripts.fb_scan import run_fb_scan

    run_fb_scan(adapter=adapter)
    queued_ids = {r["post_id"] for r in read_queue(fb_environment["queue_file"])}
    assert len(queued_ids) >= 2


def test_fb_scan_skips_duplicates(fb_environment: dict[str, Path]) -> None:
    """Posts already in the dedup cache are skipped."""
    fb_environment["dedup_file"].write_text(
        json.dumps(
            {
                "facebook": {
                    "p_dup": {
                        "engaged_at": "2099-01-01",
                        "action": "comment",
                        "status": "engaged",
                    }
                }
            }
        )
    )
    group = _group("333")
    adapter = FakeAdapter(
        "facebook",
        [group],
        {
            "333": [
                _make_fb_post("p_dup", "best dog food kibble nutrition", group),
                _make_fb_post("p_new", "best dog food kibble nutrition", group),
            ]
        },
    )

    from scripts.fb_scan import run_fb_scan

    run_fb_scan(adapter=adapter)
    queued_ids = {r["post_id"] for r in read_queue(fb_environment["queue_file"])}
    assert "p_dup" not in queued_ids
    assert "p_new" in queued_ids


def test_fb_scan_record_shape(fb_environment: dict[str, Path]) -> None:
    """Queue records have the FB shape with the expected 12 keys."""
    group = _group("444", "Dogs")
    adapter = FakeAdapter(
        "facebook",
        [group],
        {
            "444": [
                _make_fb_post(
                    "p1",
                    "best dog food kibble nutrition for puppies",
                    group,
                    category="food",
                )
            ]
        },
    )

    from scripts.fb_scan import run_fb_scan

    run_fb_scan(adapter=adapter)
    queue = read_queue(fb_environment["queue_file"])

    assert len(queue) == 1
    rec: dict[str, Any] = queue[0]
    expected_keys = {
        "platform",
        "post_url",
        "post_id",
        "post_text",
        "group_name",
        "group_url",
        "category",
        "relevance_score",
        "queued_at",
        "status",
        "requires_approval",
        "draft_comment",
    }
    assert set(rec.keys()) == expected_keys
    assert rec["platform"] == "facebook"
    assert rec["post_id"] == "p1"
    assert rec["group_name"] == "Dogs"
    assert rec["group_url"] == "https://www.facebook.com/groups/444"
    assert rec["category"] == "food"
    assert rec["status"] == "pending"
    assert isinstance(rec["requires_approval"], bool)
    assert rec["draft_comment"].startswith("DRAFT for")


def test_fb_scan_pre_filter_rejection(fb_environment: dict[str, Path]) -> None:
    """Posts rejected by the adapter's pre_filter are not queued."""
    group = _group("555")
    adapter = FakeAdapter(
        "facebook",
        [group],
        {
            "555": [
                _make_fb_post("p_keep", "best dog food kibble nutrition recipe", group),
                _make_fb_post(
                    "p_reject", "best dog food kibble nutrition recipe", group
                ),
            ]
        },
        pre_filter_overrides={"p_reject": "competitor"},
    )

    from scripts.fb_scan import run_fb_scan

    run_fb_scan(adapter=adapter)
    queued_ids = {r["post_id"] for r in read_queue(fb_environment["queue_file"])}
    assert "p_keep" in queued_ids
    assert "p_reject" not in queued_ids


def test_fb_scan_requires_approval_flag_set_below_approval_threshold(
    fb_environment: dict[str, Path],
) -> None:
    """Posts scoring between candidate and approval thresholds need approval.

    With category="general" (no group-context bonus) and comment_count=2 (no
    5-50 bonus), score lands ~0.70 — clears candidate, under approval.
    """
    group = _group("666")
    adapter = FakeAdapter(
        "facebook",
        [group],
        {
            "666": [
                _make_fb_post(
                    "p_borderline",
                    "What dog food should I feed my dog?",
                    group,
                    category="general",
                    comment_count=2,
                )
            ]
        },
    )

    from scripts.fb_scan import run_fb_scan

    run_fb_scan(adapter=adapter)
    queue = read_queue(fb_environment["queue_file"])

    assert len(queue) == 1
    rec = queue[0]
    assert rec["relevance_score"] < 0.80
    assert rec["requires_approval"] is True


def test_fb_scan_updates_last_run_on_success(
    fb_environment: dict[str, Path],
) -> None:
    """``last_run.json`` is stamped with fb_scanner success after a run."""
    group = _group("777")
    adapter = FakeAdapter(
        "facebook",
        [group],
        {"777": [_make_fb_post("p1", "best dog food kibble nutrition", group)]},
    )

    from scripts.fb_scan import run_fb_scan

    run_fb_scan(adapter=adapter)
    last_run = json.loads(fb_environment["last_run_file"].read_text())

    assert "fb_scanner" in last_run
    assert last_run["fb_scanner"]["status"] == "success"
    assert last_run["fb_scanner"]["groups_scanned"] == 1
    assert last_run["fb_scanner"]["posts_queued"] == 1
