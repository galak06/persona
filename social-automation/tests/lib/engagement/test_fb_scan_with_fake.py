"""End-to-end ``run_fb_scan()`` tests with FakeAdapter — no browser, no net.

Slice 3: FB cherry-picks top-N per day where N = quota - already-queued-today.
Slice 4 Wave 0: FB inline ``like()`` is real; ``daily_like_quota["facebook"]=5``
and ``DAILY_LIMITS["facebook:like"]=5``. The ``test_fb_scan_likes_*`` cases
lock that contract. Fixtures + ``read_queue`` helper in ``conftest.py``.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.fb_scan import run_fb_scan

from lib.engagement.adapters.fake import FakeAdapter, FakeSource
from lib.engagement.post import Post

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


def _fb_adapter(group: FakeSource, posts: list[Post], **kw: Any) -> FakeAdapter:
    return FakeAdapter("facebook", [group], {group.id: posts}, **kw)


_HIGH_SCORE_TEXT = "best ollie dog food kibble nutrition recipe?"  # 0.80
_LOW_SCORE_TEXT = "Random unrelated content about cars"  # 0.00


def _override_fb_config(
    fb_env: dict[str, Path], *, comments: int | None = None, likes: int | None = None
) -> None:
    """Rewrite FB rate-limit quotas in the tmp config file."""
    cf = fb_env["config_file"]
    payload = json.loads(cf.read_text())
    fb = payload["rate_limits"]["facebook"]
    if comments is not None:
        fb["comments_per_day"] = comments
    if likes is not None:
        fb["likes_per_day"] = likes
    cf.write_text(json.dumps(payload))


def _seed_today_record(post_id: str) -> dict[str, Any]:
    return {
        "platform": "facebook",
        "post_id": post_id,
        "queued_at": datetime.now(UTC).isoformat(),
    }


# --- tests ------------------------------------------------------------------

def test_fb_scan_queues_high_score_posts(fb_environment: dict[str, Path]) -> None:
    """High-relevance posts get queued; low-relevance posts don't."""
    group = _group("111", "Test Dog Group")
    adapter = _fb_adapter(group, [
        _make_fb_post("p1", _HIGH_SCORE_TEXT, group),
        _make_fb_post("p2", _LOW_SCORE_TEXT, group),
    ])

    report = run_fb_scan(adapter=adapter)
    queued_ids = [r["post_id"] for r in read_queue(fb_environment["queue_file"])]
    assert "p1" in queued_ids
    assert "p2" not in queued_ids
    assert report is not None and report.queued == 1


def test_fb_scan_cherry_picks_when_under_quota(fb_environment: dict[str, Path]) -> None:
    """Quota=5, 3 qualifying posts -> all 3 queue (3 < quota)."""
    group = _group("222")
    posts = [_make_fb_post(f"p{i}", _HIGH_SCORE_TEXT, group) for i in range(3)]
    run_fb_scan(adapter=_fb_adapter(group, posts))
    queued_ids = {r["post_id"] for r in read_queue(fb_environment["queue_file"])}
    assert queued_ids == {"p0", "p1", "p2"}


def test_fb_scan_cherry_picks_top_n_by_score(fb_environment: dict[str, Path]) -> None:
    """Quota=3, 7 candidates of descending score -> only top-3 queue."""
    _override_fb_config(fb_environment, comments=3)
    group = _group("333", "Score Sort Group")
    # Scores via base text-only relevance: p1=1.10, p2=0.90, p3=0.80,
    # p4/p5=0.70, p6=0.60, p7=0.40 — only p1/p2/p3 clear comment_threshold (0.75).
    texts = [
        "best fi collar gps tracker for dog food running?",
        "best dog food for running with gps tracker?",
        "best ollie dog food kibble nutrition recipe?",
        "dog food kibble nutrition gps",
        "best dog food kibble nutrition GPS",
        "What dog food should I feed my dog?",
        "best dog food kibble nutrition for puppies",
    ]
    posts = [_make_fb_post(f"p{i+1}", t, group) for i, t in enumerate(texts)]
    report = run_fb_scan(adapter=_fb_adapter(group, posts))
    queued = read_queue(fb_environment["queue_file"])
    queued_ids = [r["post_id"] for r in queued]
    assert report is not None and report.queued == 3
    assert set(queued_ids) == {"p1", "p2", "p3"}, queued_ids
    scores = [r["relevance_score"] for r in queued]
    assert scores == sorted(scores, reverse=True)


def test_fb_scan_existing_today_reduces_budget(fb_environment: dict[str, Path]) -> None:
    """Pre-seed 2 today, quota=3, scan 5 fresh -> budget=1 -> 1 appended."""
    _override_fb_config(fb_environment, comments=3)
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
    fb_environment["dedup_file"].write_text(json.dumps({"facebook": {"p_dup": {
        "engaged_at": "2099-01-01", "action": "comment", "status": "engaged",
    }}}))
    group = _group("555")
    adapter = _fb_adapter(group, [
        _make_fb_post("p_dup", _HIGH_SCORE_TEXT, group),
        _make_fb_post("p_new", _HIGH_SCORE_TEXT, group),
    ])
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
    assert (rec["platform"], rec["post_id"], rec["status"]) == (
        "facebook", "p1", "pending"
    )
    assert rec["group_name"] == "Dogs"
    assert rec["group_url"] == "https://www.facebook.com/groups/666"
    assert rec["category"] == "food"
    assert isinstance(rec["requires_approval"], bool)
    assert rec["draft_comment"].startswith("DRAFT for")


def test_fb_scan_pre_filter_rejection(fb_environment: dict[str, Path]) -> None:
    """Posts rejected by the adapter's pre_filter are not queued."""
    group = _group("777")
    adapter = _fb_adapter(group, [
        _make_fb_post("p_keep", _HIGH_SCORE_TEXT, group),
        _make_fb_post("p_reject", _HIGH_SCORE_TEXT, group),
    ], pre_filter_overrides={"p_reject": "competitor"})
    run_fb_scan(adapter=adapter)
    queued_ids = {r["post_id"] for r in read_queue(fb_environment["queue_file"])}
    assert "p_keep" in queued_ids
    assert "p_reject" not in queued_ids


def test_fb_scan_requires_approval_flag_set_below_approval_threshold(
    fb_environment: dict[str, Path],
) -> None:
    """Posts between comment_threshold and approval_threshold need approval."""
    cf = fb_environment["config_file"]
    payload = json.loads(cf.read_text())
    payload["content_analysis"]["ig_comment_threshold"] = 0.75
    payload["content_analysis"]["approval_threshold"] = 0.95
    cf.write_text(json.dumps(payload))
    group = _group("888")
    text = "best ollie dog food kibble nutrition recipe?"
    adapter = _fb_adapter(group, [
        _make_fb_post("p_borderline", text, group, category="general", comment_count=2),
    ])
    run_fb_scan(adapter=adapter)
    queue = read_queue(fb_environment["queue_file"])
    assert len(queue) == 1
    rec = queue[0]
    assert 0.75 <= rec["relevance_score"] < 0.95
    assert rec["requires_approval"] is True


def test_fb_scan_real_mark_engaged_writes_dedup(
    fb_environment_real_dedup: dict[str, Path],
) -> None:
    """Regression: production ``mark_engaged`` writes a real dedup entry.

    Slice 4 Wave 0: ``p_real`` clears the candidate-like gate, so the
    dedup entry may carry action="like" or "comment_queued" — both prove
    the production call path executed.
    """
    group = _group("999", "Real Dedup Group")
    adapter = _fb_adapter(group, [_make_fb_post("p_real", _HIGH_SCORE_TEXT, group)])
    run_fb_scan(adapter=adapter)
    dedup_cache = json.loads(fb_environment_real_dedup["dedup_file"].read_text())
    assert "facebook" in dedup_cache
    assert "p_real" in dedup_cache["facebook"]
    entry = dedup_cache["facebook"]["p_real"]
    assert entry["action"] in ("comment_queued", "like")
    assert entry["group_or_hashtag"] == "Real Dedup Group"
    assert entry["status"] == "engaged"


def test_fb_scan_updates_last_run_on_success(fb_environment: dict[str, Path]) -> None:
    """``last_run.json`` is stamped with fb_scanner success after a run."""
    group = _group("aaa")
    adapter = _fb_adapter(group, [_make_fb_post("p1", _HIGH_SCORE_TEXT, group)])
    run_fb_scan(adapter=adapter)
    last_run = json.loads(fb_environment["last_run_file"].read_text())
    assert "fb_scanner" in last_run
    assert last_run["fb_scanner"]["status"] == "success"
    assert last_run["fb_scanner"]["groups_scanned"] == 1
    assert last_run["fb_scanner"]["posts_queued"] == 1


# --- Slice 4 Wave 0: FB inline like contract --------------------------------

def test_fb_scan_likes_qualifying_posts(fb_environment: dict[str, Path]) -> None:
    """Every FB post clearing candidate_threshold gets ``like()`` invoked."""
    group = _group("bbb", "FB Like Group")
    posts = [_make_fb_post(f"p{i}", _HIGH_SCORE_TEXT, group) for i in range(3)]
    run_fb_scan(adapter=(adapter := _fb_adapter(group, posts)))
    attempted = {p.post_id for p in adapter.likes_attempted}
    succeeded = {p.post_id for p in adapter.likes_succeeded}
    assert attempted == {"p0", "p1", "p2"}
    assert succeeded == {"p0", "p1", "p2"}


def test_fb_scan_skips_likes_when_below_candidate_threshold(
    fb_environment: dict[str, Path],
) -> None:
    """Low-score posts never reach the like step; only candidates are liked."""
    group = _group("ccc", "Mixed Score Group")
    adapter = _fb_adapter(group, [
        _make_fb_post("p_low", _LOW_SCORE_TEXT, group),
        _make_fb_post("p_high", _HIGH_SCORE_TEXT, group),
    ])
    run_fb_scan(adapter=adapter)
    attempted = {p.post_id for p in adapter.likes_attempted}
    assert attempted == {"p_high"}


def test_fb_scan_likes_respect_daily_like_quota(
    fb_environment: dict[str, Path],
) -> None:
    """``daily_like_quota["facebook"] = 0`` suppresses every like attempt.

    Inverse case (default quota=5, 3 posts) is locked in
    ``test_fb_scan_likes_qualifying_posts``.
    """
    _override_fb_config(fb_environment, likes=0)
    group = _group("ddd", "No-Like Group")
    posts = [_make_fb_post(f"p{i}", _HIGH_SCORE_TEXT, group) for i in range(3)]
    run_fb_scan(adapter=(adapter := _fb_adapter(group, posts)))
    assert adapter.likes_attempted == []
    assert adapter.likes_succeeded == []
    # Comment queue still populated — the quota gate is like-specific.
    queued_ids = {r["post_id"] for r in read_queue(fb_environment["queue_file"])}
    assert queued_ids == {"p0", "p1", "p2"}
