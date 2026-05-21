"""End-to-end ``run_fb_scan()`` tests with FakeAdapter — no browser, no net.

Slice 3: FB cherry-picks top-N per day where N = quota - already-queued-today.
Fixture (``fb_environment``) + ``read_queue`` helper live in ``conftest.py``.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lib.engagement.adapters.fake import FakeAdapter, FakeSource
from lib.engagement.post import Post
from scripts.fb_scan import run_fb_scan

from .conftest import read_queue


# --- helpers ----------------------------------------------------------------


def _group(group_id: str, name: str = "G") -> FakeSource:
    return FakeSource(
        id=group_id, name=name, url=f"https://www.facebook.com/groups/{group_id}"
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


def _fb_adapter(
    group: FakeSource, posts: list[Post], **kw: Any
) -> FakeAdapter:
    return FakeAdapter("facebook", [group], {group.id: posts}, **kw)


# The new pipeline passes only post text to ``score_relevance`` (no
# meta / group_category bonus), so test fixtures hit threshold via text alone.
_HIGH_SCORE_TEXT = "best ollie dog food kibble nutrition recipe?"  # 0.80
_LOW_SCORE_TEXT = "Random unrelated content about cars"  # 0.00


def _override_fb_quota(fb_env: dict[str, Path], quota: int) -> None:
    """Rewrite the FB ``comments_per_day`` quota in the tmp config file."""
    config_file = fb_env["config_file"]
    payload = json.loads(config_file.read_text())
    payload["rate_limits"]["facebook"]["comments_per_day"] = quota
    config_file.write_text(json.dumps(payload))


def _seed_today_record(post_id: str) -> dict[str, Any]:
    """Minimal FB queue record stamped today for budget arithmetic."""
    return {
        "platform": "facebook",
        "post_id": post_id,
        "queued_at": datetime.now(UTC).isoformat(),
    }


# --- tests ------------------------------------------------------------------


def test_fb_scan_queues_high_score_posts(fb_environment: dict[str, Path]) -> None:
    """High-relevance posts get queued; low-relevance posts don't."""
    group = _group("111", "Test Dog Group")
    adapter = _fb_adapter(
        group,
        [
            _make_fb_post("p1", _HIGH_SCORE_TEXT, group),
            _make_fb_post("p2", _LOW_SCORE_TEXT, group),
        ],
    )

    report = run_fb_scan(adapter=adapter)
    queued_ids = [r["post_id"] for r in read_queue(fb_environment["queue_file"])]
    assert "p1" in queued_ids
    assert "p2" not in queued_ids
    assert report is not None and report.queued == 1


def test_fb_scan_cherry_picks_when_under_quota(
    fb_environment: dict[str, Path],
) -> None:
    """Quota=5, 3 qualifying posts -> all 3 queue (3 < quota).

    Renamed from slice-2's ``test_fb_scan_inline_queueing_multiple_posts``:
    the assertion is unchanged but the semantic is now "cherry-pick has
    headroom" rather than "inline queue every match".
    """
    group = _group("222")
    posts = [_make_fb_post(f"p{i}", _HIGH_SCORE_TEXT, group) for i in range(3)]

    run_fb_scan(adapter=_fb_adapter(group, posts))
    queued_ids = {r["post_id"] for r in read_queue(fb_environment["queue_file"])}
    assert queued_ids == {"p0", "p1", "p2"}


def test_fb_scan_cherry_picks_top_n_by_score(
    fb_environment: dict[str, Path],
) -> None:
    """Quota=3, 7 candidates of descending score -> only top-3 queue.

    Proves the pipeline orders by score before applying the daily budget.
    """
    _override_fb_quota(fb_environment, quota=3)
    group = _group("333", "Score Sort Group")
    # Scores via base text-only relevance: p1=1.10, p2=0.90, p3=0.80,
    # p4/p5=0.70, p6=0.60, p7=0.40 — only p1/p2/p3 clear comment_threshold (0.75).
    posts = [
        _make_fb_post("p1", "best fi collar gps tracker for dog food running?", group),
        _make_fb_post("p2", "best dog food for running with gps tracker?", group),
        _make_fb_post("p3", "best ollie dog food kibble nutrition recipe?", group),
        _make_fb_post("p4", "dog food kibble nutrition gps", group),
        _make_fb_post("p5", "best dog food kibble nutrition GPS", group),
        _make_fb_post("p6", "What dog food should I feed my dog?", group),
        _make_fb_post("p7", "best dog food kibble nutrition for puppies", group),
    ]

    report = run_fb_scan(adapter=_fb_adapter(group, posts))
    queued = read_queue(fb_environment["queue_file"])
    queued_ids = [r["post_id"] for r in queued]
    assert report is not None and report.queued == 3
    # Top 3 by score are unambiguously p1 > p2 > p3 (no ties at the top).
    assert set(queued_ids) == {"p1", "p2", "p3"}, (
        f"Cherry-pick should select p1/p2/p3, got {queued_ids}"
    )
    scores = [r["relevance_score"] for r in queued]
    assert scores == sorted(scores, reverse=True)


def test_fb_scan_existing_today_reduces_budget(
    fb_environment: dict[str, Path],
) -> None:
    """Pre-seed 2 today, quota=3, scan 5 fresh -> budget=1 -> 1 appended."""
    _override_fb_quota(fb_environment, quota=3)
    pre_seeded = [_seed_today_record("seed_a"), _seed_today_record("seed_b")]
    fb_environment["queue_file"].write_text(json.dumps(pre_seeded))

    group = _group("444", "Fresh Group")
    posts = [_make_fb_post(f"f{i}", _HIGH_SCORE_TEXT, group) for i in range(5)]

    report = run_fb_scan(adapter=_fb_adapter(group, posts))
    queue = read_queue(fb_environment["queue_file"])
    new_ids = {r["post_id"] for r in queue} - {"seed_a", "seed_b"}
    assert report is not None and report.queued == 1
    assert len(new_ids) == 1
    assert {"seed_a", "seed_b"}.issubset({r["post_id"] for r in queue})


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
    group = _group("555")
    adapter = _fb_adapter(
        group,
        [
            _make_fb_post("p_dup", _HIGH_SCORE_TEXT, group),
            _make_fb_post("p_new", _HIGH_SCORE_TEXT, group),
        ],
    )

    run_fb_scan(adapter=adapter)
    queued_ids = {r["post_id"] for r in read_queue(fb_environment["queue_file"])}
    assert "p_dup" not in queued_ids
    assert "p_new" in queued_ids


def test_fb_scan_record_shape(fb_environment: dict[str, Path]) -> None:
    """Queue records have the FB shape with the expected 12 keys."""
    group = _group("666", "Dogs")
    adapter = _fb_adapter(
        group, [_make_fb_post("p1", _HIGH_SCORE_TEXT, group, category="food")]
    )

    run_fb_scan(adapter=adapter)
    queue = read_queue(fb_environment["queue_file"])
    assert len(queue) == 1
    rec: dict[str, Any] = queue[0]
    assert set(rec.keys()) == {
        "platform", "post_url", "post_id", "post_text", "group_name", "group_url",
        "category", "relevance_score", "queued_at", "status", "requires_approval",
        "draft_comment",
    }
    assert (rec["platform"], rec["post_id"], rec["status"]) == ("facebook", "p1", "pending")
    assert rec["group_name"] == "Dogs"
    assert rec["group_url"] == "https://www.facebook.com/groups/666"
    assert rec["category"] == "food"
    assert isinstance(rec["requires_approval"], bool)
    assert rec["draft_comment"].startswith("DRAFT for")


def test_fb_scan_pre_filter_rejection(fb_environment: dict[str, Path]) -> None:
    """Posts rejected by the adapter's pre_filter are not queued."""
    group = _group("777")
    adapter = _fb_adapter(
        group,
        [
            _make_fb_post("p_keep", _HIGH_SCORE_TEXT, group),
            _make_fb_post("p_reject", _HIGH_SCORE_TEXT, group),
        ],
        pre_filter_overrides={"p_reject": "competitor"},
    )

    run_fb_scan(adapter=adapter)
    queued_ids = {r["post_id"] for r in read_queue(fb_environment["queue_file"])}
    assert "p_keep" in queued_ids
    assert "p_reject" not in queued_ids


def test_fb_scan_requires_approval_flag_set_below_approval_threshold(
    fb_environment: dict[str, Path],
) -> None:
    """Posts between comment_threshold and approval_threshold need approval.

    ``score_relevance`` only emits multiples of 0.10, so we set
    ``ig_comment_threshold=0.70`` and ``approval_threshold=0.85`` —
    a 0.80 text then clears the comment gate but falls under approval.
    """
    config_file = fb_environment["config_file"]
    payload = json.loads(config_file.read_text())
    payload["content_analysis"]["ig_comment_threshold"] = 0.70
    payload["content_analysis"]["approval_threshold"] = 0.85
    config_file.write_text(json.dumps(payload))

    group = _group("888")
    # food (0.40) + brand "ollie" (0.20) + question (0.20) = 0.80
    text_at_080 = "best ollie dog food kibble nutrition recipe?"
    adapter = _fb_adapter(group, [_make_fb_post("p_borderline", text_at_080, group)])

    run_fb_scan(adapter=adapter)
    queue = read_queue(fb_environment["queue_file"])
    assert len(queue) == 1
    rec = queue[0]
    assert rec["relevance_score"] < 0.85
    assert rec["requires_approval"] is True


def test_fb_scan_real_mark_engaged_writes_dedup(
    fb_environment_real_dedup: dict[str, Path],
) -> None:
    """Regression: production ``mark_engaged`` writes the legacy
    ``comment_queued`` marker the post-pipeline scanner still emits for FB.
    """
    group = _group("999", "Real Dedup Group")
    adapter = _fb_adapter(group, [_make_fb_post("p_real", _HIGH_SCORE_TEXT, group)])

    run_fb_scan(adapter=adapter)
    dedup_cache = json.loads(fb_environment_real_dedup["dedup_file"].read_text())
    assert "facebook" in dedup_cache
    assert "p_real" in dedup_cache["facebook"]
    entry = dedup_cache["facebook"]["p_real"]
    assert entry["action"] == "comment_queued"
    assert entry["group_or_hashtag"] == "Real Dedup Group"
    assert entry["status"] == "engaged"


def test_fb_scan_updates_last_run_on_success(
    fb_environment: dict[str, Path],
) -> None:
    """``last_run.json`` is stamped with fb_scanner success after a run."""
    group = _group("aaa")
    adapter = _fb_adapter(group, [_make_fb_post("p1", _HIGH_SCORE_TEXT, group)])

    run_fb_scan(adapter=adapter)
    last_run = json.loads(fb_environment["last_run_file"].read_text())
    assert "fb_scanner" in last_run
    assert last_run["fb_scanner"]["status"] == "success"
    assert last_run["fb_scanner"]["groups_scanned"] == 1
    assert last_run["fb_scanner"]["posts_queued"] == 1
