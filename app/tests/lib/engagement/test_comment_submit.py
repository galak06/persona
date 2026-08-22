"""Claim -> submit -> settle: the ordering that stops duplicate comments.

`scripts/ig_engager.py` put TWO identical comments on one Instagram post. The
chain: `lib/ig/comment_post.py` clicked Post and returned True unconditionally,
`lib/engagement/adapters/instagram.py:237-238` flattened a post-click exception
into `CommentResult.failed(...)`, and `inline_comment.py` marked the post
deduped only AFTER a reported success. So a comment that LANDED but was
reported failed stayed retryable, and the next run wrote it again.

Nothing observed after the fact can tell "failed to post" and "posted, then
crashed" apart. `lib/engagement/comment_submit.py` therefore stops trying: it
takes a durable claim BEFORE the network call, and the claim — not the result
— is what refuses the second attempt.

The regression test for the actual bug is
`test_landed_but_reported_failed_is_not_commented_again`; everything else here
guards one edge of the ordering.

Fakes + factories live in `_pipeline_fakes`. The conftest's autouse
`_hermetic_engagement_sinks` fixture no-ops `log_engagement` and
`record_publish` for every test in this directory; the one test that asserts on
`record_publish` re-patches it with a recorder.
"""

from __future__ import annotations

from typing import Any

import pytest

import lib.engagement.comment_submit as comment_submit
from lib.engagement.adapters.fake import FakeAdapter
from lib.engagement.result import CommentResult
from tests.lib.engagement._pipeline_fakes import (
    FakeDedup,
    FakeIterateOnceDedup,
    FakeLog,
    make_ig_posts,
    make_post,
    make_src,
    run,
    stub_score,
)


def _ig_adapter(n: int = 1, **kwargs: Any) -> FakeAdapter:
    """IG adapter with ``n`` high-score (0.85), question-form posts."""
    return FakeAdapter("instagram", [make_src("s1")], {"s1": make_ig_posts(n)}, **kwargs)


class _LandedButReportedFailedAdapter(FakeAdapter):
    """The exact shape of the bug: the comment goes out, the result says failed.

    Models `post_comment_ig` returning after its submit click and *then*
    something raising — `InstagramHashtagAdapter.comment` catches it and
    returns `CommentResult.failed("exception:TimeoutError")` for a comment
    that is, at that moment, live on the post.
    """

    def comment(self, post: Any, text: str) -> CommentResult:
        self.comments.append((post.post_id, text))
        return CommentResult.failed("exception:TimeoutError")


class _ClaimlessDedup:
    """A dedup collaborator with no claim capability (the bare `deduplication`
    module's shape). It satisfies `Dedup` and nothing more."""

    def __init__(self) -> None:
        self.engaged: list[tuple[str, str, str]] = []

    def is_duplicate(self, platform: str, post_id: str) -> bool:
        return False

    def mark_engaged(
        self,
        platform: str,
        post_id: str,
        action: str,
        group_or_hashtag: str = "",
        status: str = "engaged",
    ) -> None:
        self.engaged.append((platform, post_id, action))


def _events(log: FakeLog) -> list[str]:
    """The leading event token of every log line the run emitted."""
    return [msg.split(" ", 1)[0] for _level, msg in log.calls]


# --- the regression for the actual duplicate-comment bug ---------------------


def test_landed_but_reported_failed_is_not_commented_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bug, end to end: a landed-but-reported-failed comment stays claimed.

    Run 1 submits a comment that really lands and comes back
    `CommentResult.failed(...)`. Run 2 must NOT write a second one — and it
    must be the claim that stops it, not the seen-mark (the post is
    deliberately left unmarked so `comment_verify` can still release it).
    """
    rows: list[dict[str, Any]] = []
    monkeypatch.setattr(
        comment_submit.engagements_db,
        "record_publish",
        lambda **kwargs: rows.append(kwargs),
    )
    dedup = FakeIterateOnceDedup()
    first = _LandedButReportedFailedAdapter("instagram", [make_src("s1")], {"s1": make_ig_posts(1)})
    report, _d, _rt, _dr = run(first, dedup=dedup, inline_comment=True)

    # (a) the claim was taken before the submit and is still held
    assert dedup.claims == [("instagram", "p0")]
    assert ("instagram", "p0") in dedup.claimed
    assert ("instagram", "p0") not in dedup.settled, "unconfirmed: still pending"
    # (b) nothing was written to engagements — a `failed` row would have
    #     upserted onto the SAME primary key as the `posted` row
    assert rows == [], "an unconfirmed submission must write no engagements row"
    assert [pid for pid, _t in first.comments] == ["p0"]
    assert report.comments_posted == 0
    # (c) the post is NOT marked seen, so `comment_verify` can still release it
    assert dedup.seen_marked == []

    # Run 2, same dedup: the claim refuses the post before anything is drafted.
    assert dedup.is_duplicate("instagram", "p0") is True
    second = _LandedButReportedFailedAdapter(
        "instagram", [make_src("s1")], {"s1": make_ig_posts(1)}
    )
    run(second, dedup=dedup, inline_comment=True)

    assert second.comments == [], "the post was commented on a SECOND time"
    assert len(first.comments) == 1


# --- ordering: claim first, settle after --------------------------------------


def test_claim_is_taken_before_the_network_call() -> None:
    """The claim must precede `commenter.comment`, not follow its result."""
    order: list[str] = []

    class _OrderRecordingDedup(FakeIterateOnceDedup):
        def claim_comment(self, platform: str, post_id: str, **kwargs: Any) -> bool:
            order.append("claim")
            return super().claim_comment(platform, post_id, **kwargs)

    class _OrderRecordingAdapter(FakeAdapter):
        def comment(self, post: Any, text: str) -> CommentResult:
            order.append("comment")
            return super().comment(post, text)

    adapter = _OrderRecordingAdapter("instagram", [make_src("s1")], {"s1": make_ig_posts(1)})
    run(adapter, dedup=_OrderRecordingDedup(), inline_comment=True)

    assert order == ["claim", "comment"], "the claim must be durable BEFORE the submit"


def test_settle_follows_a_confirmed_post() -> None:
    """A confirmed comment flips its claim from `pending` to `posted`."""
    dedup = FakeIterateOnceDedup()
    report, _d, _rt, _dr = run(_ig_adapter(1), dedup=dedup, inline_comment=True)

    assert report.comments_posted == 1
    assert ("instagram", "p0") in dedup.settled


# --- refusals: nothing is submitted without a granted claim -------------------


def test_refused_claim_skips_the_submission_entirely() -> None:
    """`claim_comment` -> False means someone owns the post: do not comment."""
    adapter = _ig_adapter(1)
    dedup = FakeIterateOnceDedup()
    dedup.claim_grants = False
    log = FakeLog()
    report, _d, rt, _dr = run(adapter, dedup=dedup, log=log, inline_comment=True)

    assert adapter.comments == []
    assert report.comments_attempted == 0, "a blocked post was never attempted"
    assert report.comments_posted == 0
    assert ("instagram", "comment") not in rt.recorded
    assert "comment_claim_refused" in _events(log)


def test_a_blocked_post_is_left_retryable() -> None:
    """Nothing was submitted, so the next run must be allowed to try again."""
    dedup = FakeIterateOnceDedup()
    dedup.claim_grants = False
    run(_ig_adapter(1), dedup=dedup, inline_comment=True)

    assert dedup.seen_marked == [], "a blocked comment must not retire the post"


def test_dedup_without_claim_capability_refuses_to_comment() -> None:
    """No claim capability -> no comment. The inverse of `SupportsMarkSeen`.

    A missing `mark_seen` degrades open (worst case: a re-visit); a missing
    claim degrades CLOSED, because the failure it would allow is a duplicate
    comment on a stranger's post.
    """
    adapter = _ig_adapter(1)
    log = FakeLog()
    report, _d, rt, _dr = run(adapter, dedup=_ClaimlessDedup(), log=log, inline_comment=True)

    assert adapter.comments == [], "commented without a durable claim"
    assert report.comments_posted == 0
    assert ("instagram", "comment") not in rt.recorded
    assert "comment_claim_unsupported" in _events(log)


def test_a_claim_outage_skips_the_draft_too() -> None:
    """`claims_available()` is asked BEFORE the LLM call, not after it.

    A Postgres outage must not be paid for with a drafting call whose comment
    the claim is then going to refuse anyway.
    """
    adapter = _ig_adapter(1)
    dedup = FakeIterateOnceDedup()
    dedup.claims_ok = False
    log = FakeLog()
    report, _d, _rt, drafter = run(adapter, dedup=dedup, log=log, inline_comment=True)

    assert drafter.calls == [], "an outage must not spend an LLM call"
    assert dedup.claims == [], "the claim was never even attempted"
    assert adapter.comments == []
    assert report.comments_posted == 0
    assert "comment_skipped_claims_unavailable" in _events(log)


# --- a settle failure is not a comment failure --------------------------------


def test_settle_failure_does_not_turn_a_posted_comment_into_a_failure() -> None:
    """The comment is live; the claim just stayed `pending`. Report the truth.

    The claim being unsettled still blocks a second comment, so nothing is at
    risk — and calling a landed comment "failed" is exactly the confusion this
    design removes.
    """
    adapter = _ig_adapter(1)
    dedup = FakeIterateOnceDedup()
    dedup.settle_ok = False
    log = FakeLog()
    report, _d, rt, _dr = run(adapter, dedup=dedup, log=log, inline_comment=True)

    assert report.comments_posted == 1, "a settle failure is not a comment failure"
    assert adapter.comments == [("p0", "DRAFT for https://x/p/p0")]
    assert ("instagram", "comment") in rt.recorded, "the budget was still spent"
    assert "comment_claim_unsettled" in _events(log)
    assert ("instagram", "p0") in dedup.seen_marked, "a posted comment is terminal"


# --- the unconfirmed path writes nothing to engagements -----------------------


def test_failed_submission_writes_nothing_to_engagements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No `record_publish` at all on the unconfirmed path — see the module doc."""
    rows: list[dict[str, Any]] = []
    monkeypatch.setattr(
        comment_submit.engagements_db,
        "record_publish",
        lambda **kwargs: rows.append(kwargs),
    )
    run(
        _ig_adapter(1, comment_should_fail=True),
        dedup=FakeIterateOnceDedup(),
        inline_comment=True,
    )

    assert rows == []


def test_a_posted_comment_still_writes_its_engagements_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `posted` row is the ONLY `engagements` write left on this path."""
    rows: list[dict[str, Any]] = []
    monkeypatch.setattr(
        comment_submit.engagements_db,
        "record_publish",
        lambda **kwargs: rows.append(kwargs),
    )
    run(_ig_adapter(1), dedup=FakeIterateOnceDedup(), inline_comment=True)

    assert [row["status"] for row in rows] == ["posted"]


# --- the claim carries what the reconciler needs ------------------------------


def test_the_claim_carries_the_text_and_url_the_reconciler_needs() -> None:
    """`comment_verify` resolves a stale claim by reopening `post_url` and
    searching the DOM for `content`, so both must reach the claim store."""
    captured: list[dict[str, Any]] = []

    class _CapturingDedup(FakeIterateOnceDedup):
        def claim_comment(self, platform: str, post_id: str, **kwargs: Any) -> bool:
            captured.append(kwargs)
            return super().claim_comment(platform, post_id, **kwargs)

    post = make_post("p0", "food question?", source_name="hashtag_dogs")
    adapter = FakeAdapter("instagram", [make_src("s1")], {"s1": [post]})
    run(adapter, dedup=_CapturingDedup(), inline_comment=True, score=stub_score)

    assert len(captured) == 1
    assert captured[0]["post_url"] == "https://x/p/p0"
    assert captured[0]["target_name"] == "hashtag_dogs"
    assert captured[0]["content"] == "DRAFT for https://x/p/p0"


def test_plain_fake_dedup_can_still_comment() -> None:
    """Sanity check on the fakes themselves: `FakeDedup` claims, so it posts.

    Without this the whole suite could go green having posted nothing — the
    exact failure mode the claim gate introduces.
    """
    adapter = _ig_adapter(1)
    report, _d, _rt, _dr = run(adapter, dedup=FakeDedup(), inline_comment=True)

    assert report.comments_posted == 1
    assert adapter.comments == [("p0", "DRAFT for https://x/p/p0")]
