"""End-to-end tests for ``run_fb_engager_scan()`` via FakeAdapter — no browser.

Facebook is SINGLE-PASS now: ``run_fb_engager_scan`` wires the shared
pipeline with ``inline_comment=True``, so each qualifying group post is
scored, liked, and commented in the ONE visit that opened it — the retired
two-stage ``fb_scan.py`` -> queue -> ``fb_comment.py`` handoff is gone.
These tests run the ``run_fb_engager_scan`` ENTRY POINT, covering the
wiring the pipeline fakes can't see: ``config.json`` -> policy + the real
``score_relevance`` scorer / ``rate_limiter`` / ``ScanDedup``; the
``WarmFilteredAdapter`` wrap (GAP-1 — the engager warm-wraps WHATEVER
adapter it is given, injected fakes included); the absence of any human
approval gate (a brand-new group comments inline and notifies nobody —
the per-group first-comment gate was removed 2026-08-13 for parity with
ig-engager); persistence (``record_publish`` row, dedup mark, last-run
stamp); and dry run (nothing posted, no state written).

Unit coverage: ``test_pipeline_inline_comment`` /
``test_pipeline_record_publish``. Fixture in ``conftest.py`` /
``_env_builders.py`` (warmup defaults to warm — tests below re-patch it).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from scripts import fb_engager
from scripts.fb_engager import run_fb_engager_scan

import lib.group_warmup as group_warmup
import lib.groups_db as groups_db
import notifier
from lib.engagement.adapters.fake import FakeAdapter, FakeSource
from lib.engagement.post import Post


@pytest.fixture(autouse=True)
def _stub_skill_drafter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the skill-bound drafter instance the scan actually calls.

    ``run_fb_engager_scan`` passes ``fb_engager._DRAFTER`` (bound to the
    fb-engager SKILL.md at import) into the pipeline, so patch the instance
    method (same pattern as ``test_ig_engager_with_fake``).
    """

    def _fake_draft(
        *, platform: str, post_text: str, group_or_hashtag: str | None,
        post_url: str, site_context: str | None = None,
    ) -> str:
        return f"DRAFT-{post_url or '?'}"

    monkeypatch.setattr(fb_engager._DRAFTER, "draft_comment_for_post", _fake_draft)


# --- helpers ----------------------------------------------------------------


def _group(gid: str) -> FakeSource:
    return FakeSource(id=gid, name=gid, url=f"https://www.facebook.com/groups/{gid}")


def _make_fb_post(post_id: str, text: str, group: FakeSource) -> Post:
    """Build an FB group Post with realistic platform_extra metadata."""
    return Post(
        platform="facebook",
        post_id=post_id,
        post_url=f"https://www.facebook.com/groups/{group.id}/posts/{post_id}/",
        text=text,
        author="Jane Doe",
        source_id=group.id,
        source_name=group.name,
        source_url=group.url,
        platform_extra={"category": "food", "comment_count": 12},
    )


def _high_score_text() -> str:
    """Question scoring well above auto-approve (0.80) via the REAL scorer:
    primary keywords + competitor 'ollie' + '?' + fresh/engaged meta."""
    return "best ollie dog food kibble nutrition recipe for my dog?"


def _single_post_adapter(gid: str, post: Post) -> FakeAdapter:
    return FakeAdapter("facebook", [_group(gid)], {gid: [post]})


# --- 1. the single-pass headline: like AND comment in one visit --------------


def test_fb_scan_likes_and_comments_inline_single_pass(
    fb_environment: dict[str, Any],
) -> None:
    """One visit: like AND comment — plus dedup mark and last-run stamp."""
    group = _group("doggroup")
    post = _make_fb_post("p1", _high_score_text(), group)
    adapter = _single_post_adapter("doggroup", post)

    report = run_fb_engager_scan(adapter=adapter)
    assert report is not None
    # One visit, both actions.
    assert [p.post_id for p in adapter.likes_succeeded] == ["p1"]
    assert [post_id for post_id, _text in adapter.comments] == ["p1"]
    assert report.likes_succeeded == 1
    assert report.comments_posted == 1
    assert report.queued == 0, "single-pass never queues"

    # The engaged mark landed in the (tmp) JSON dedup cache...
    dedup = json.loads(fb_environment["dedup_file"].read_text())
    assert dedup["facebook"]["p1"]["action"] == "comment"
    # ...and the last-run stamp carries the single-pass counters.
    last_run = json.loads(fb_environment["last_run_file"].read_text())
    fb = last_run["fb_engager"]
    assert fb["status"] == "success"
    assert fb["groups_scanned"] == 1
    assert fb["posts_liked"] == 1
    assert fb["posts_commented"] == 1


# --- 2. warmup: newly joined groups are excluded (GAP-1) ---------------------


def test_fb_scan_excludes_groups_inside_comment_warmup(
    fb_environment: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A group joined <48h ago is filtered out before any post is opened.
    The engager warm-wraps the INJECTED adapter too, and the real
    ``is_group_warm`` logic runs — only the tracker read is patched."""
    warm, cold = _group("oldtimers"), _group("justjoined")
    posts = {
        "oldtimers": [_make_fb_post("p_warm", _high_score_text(), warm)],
        "justjoined": [_make_fb_post("p_cold", _high_score_text(), cold)],
    }
    adapter = FakeAdapter("facebook", [warm, cold], posts)
    one_hour_ago = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    monkeypatch.setattr(
        group_warmup,
        "_load_tracker",
        lambda: [{"group_url": cold.url, "joined_at": one_hour_ago}],
    )

    report = run_fb_engager_scan(adapter=adapter)
    assert report is not None
    assert report.sources_visited == 1, "the cold group must not be visited"
    assert [p.post_id for p in adapter.likes_succeeded] == ["p_warm"]
    assert [post_id for post_id, _t in adapter.comments] == ["p_warm"]


# --- 3. no human approval gate (parity with ig-engager) ----------------------


def test_fb_scan_comments_in_a_brand_new_group_with_no_approval(
    fb_environment: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A group nobody ever approved comments inline anyway, silently.

    The per-group first-comment approval gate was removed on 2026-08-13 so
    fb-engager matches ig-engager: the drafter's engage/decline decision is
    the only thing standing between a candidate post and a live comment. A
    group the groups DB has never heard of (``get_by_url`` -> None, which
    the retired gate treated as fail-closed) must therefore still comment,
    and must send no Telegram message.
    """
    group = _group("newgroup")
    posts = [
        _make_fb_post("p1", _high_score_text(), group),
        _make_fb_post("p2", _high_score_text(), group),
    ]
    adapter = FakeAdapter("facebook", [group], {"newgroup": posts})

    sent: list[str] = []
    monkeypatch.setattr(groups_db, "get_by_url", lambda _url: None)
    monkeypatch.setattr(notifier, "send", lambda msg, **_k: sent.append(msg))

    report = run_fb_engager_scan(adapter=adapter)
    assert report is not None
    assert [post_id for post_id, _t in adapter.comments] == ["p1", "p2"]
    assert report.comments_posted == 2
    assert [p.post_id for p in adapter.likes_succeeded] == ["p1", "p2"]
    assert sent == []  # no approval request, ever


# --- 4. persistence: engagements_db row --------------------------------------


def test_fb_scan_records_publish_row(
    fb_environment: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A posted inline comment is persisted via ``record_publish``."""
    import lib.engagement.inline_comment as inline_comment

    rows: list[dict[str, Any]] = []
    monkeypatch.setattr(
        inline_comment.engagements_db,
        "record_publish",
        lambda **kwargs: rows.append(kwargs),
    )
    post = _make_fb_post("p1", _high_score_text(), _group("doggroup"))
    run_fb_engager_scan(adapter=_single_post_adapter("doggroup", post))

    assert len(rows) == 1
    assert rows[0]["platform"] == "facebook"
    assert rows[0]["kind"] == "comment"
    assert rows[0]["status"] == "posted"
    assert rows[0]["ref"] == "p1"


# --- 5. dry run posts nothing and writes nothing ------------------------------


def test_fb_scan_dry_run_touches_no_state(
    fb_environment: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``dry_run=True``: nothing liked or commented, no state written."""
    group = _group("newgroup")
    post = _make_fb_post("p1", _high_score_text(), group)
    adapter = _single_post_adapter("newgroup", post)

    sent: list[str] = []
    monkeypatch.setattr(notifier, "send", lambda msg, **_k: sent.append(msg))

    report = run_fb_engager_scan(adapter=adapter, dry_run=True)
    assert report is not None
    # Nothing left the process.
    assert adapter.likes_attempted == []
    assert adapter.comments == []
    # ...but the report shows the would-be like.
    assert report.likes_attempted == 1
    # No engagement state consumed: no last-run stamp, dedup mark, or
    # like/comment budget spend. (KNOWN GAP, deliberately not asserted:
    # `post_processor.gate_source` records a facebook `group_visit` even
    # under dry_run; tighten to `== {}` once gate_source learns dry_run.)
    assert json.loads(fb_environment["last_run_file"].read_text()) == {}
    assert json.loads(fb_environment["dedup_file"].read_text()) == {}
    rate_state = json.loads(fb_environment["rate_limit_file"].read_text())
    spent_keys = {key for counts in rate_state.values() for key in counts}
    assert "facebook:like" not in spent_keys
    assert "facebook:comment" not in spent_keys
    assert sent == []
