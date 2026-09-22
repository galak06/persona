"""Pipeline for OutboundEngagement.

`run_outbound_scan` glues an `OutboundAdapter` to platform-agnostic
collaborators (dedup, rate tracker, drafter, comment gate, log) and
returns a `ScanReport`. Both `scripts/fb_engager.py` and
`scripts/ig_engager.py` are thin wrappers around it, in single-pass mode
(`inline_comment=True`): each post is opened once and liked AND commented
in that same visit. The old Facebook two-stage mode (collect candidates,
cherry-pick into a queue file, drain later) is retired; `inline_comment`
defaults to False only as a like-only degrade.

This module owns orchestration only. The collaborator protocols, result
types, per-post processing and the inline comment step each live in their
own module (the file-size cap is 300 lines); the protocols are
re-exported below under their historical underscore-prefixed names so
existing imports keep working.
"""

from __future__ import annotations

from collections.abc import Callable

from lib.engagement.adapter import OutboundAdapter, SupportsComment
from lib.engagement.collaborators import (
    CommentGate as _CommentGate,
)
from lib.engagement.collaborators import (
    Dedup as _Dedup,
)
from lib.engagement.collaborators import (
    Drafter as _Drafter,
)
from lib.engagement.collaborators import (
    Log as _Log,
)
from lib.engagement.collaborators import (
    RateTracker as _RateTracker,
)
from lib.engagement.collaborators import (
    SupportsMarkSeen as _SupportsMarkSeen,
)
from lib.engagement.extraction import (
    EXTRACTION_EMPTY_CAPTION,
    EXTRACTION_FAILED,
    extraction_status,
)
from lib.engagement.policy import EngagementPolicy
from lib.engagement.post import Post
from lib.engagement.post_processor import gate_source, process_post
from lib.engagement.scan_deadline import (
    STOPPED_DEADLINE,
    STOPPED_RATE_LIMIT,
    ScanDeadline,
)
from lib.engagement.scan_results import PostOutcome, ScanReport

__all__ = [
    "ScanReport",
    "_CommentGate",
    "_Dedup",
    "_Drafter",
    "_Log",
    "_RateTracker",
    "_SupportsMarkSeen",
    "run_outbound_scan",
]


def run_outbound_scan(
    adapter: OutboundAdapter,
    policy: EngagementPolicy,
    *,
    dedup: _Dedup,
    rate_tracker: _RateTracker,
    drafter: _Drafter | None,
    log: _Log,
    now_iso: Callable[[], str],
    score_relevance: Callable[[Post], float],
    dry_run: bool = False,
    inline_comment: bool = False,
    comment_gate: _CommentGate | None = None,
    deadline: ScanDeadline | None = None,
) -> ScanReport:
    """Run one outbound-engagement scan and return a `ScanReport`.

    The adapter owns platform mechanics (session, source enumeration, post
    extraction, pre-filter, score adjustment, inline like/comment). The
    pipeline owns orchestration: dedup gating, scoring, rate limits, and
    the inline comment step.

    Commenting requires `inline_comment=True`, an adapter implementing
    `SupportsComment` and a non-None `drafter`; if any is missing the scan
    degrades to like-only (and logs `inline_comment_unavailable` once when
    inline mode was requested). An optional `comment_gate` can veto
    individual comments — see `lib/engagement/collaborators.py`; the like
    step is never gated.

    `now_iso` is retained for the wrappers' call sites (collaborators such
    as the first-comment gate take their own clock).

    When `dry_run` is True the scan is read-only: `adapter.like` and
    `adapter.comment` are never called (the post records a
    `LikeResult.skipped("dry_run")` instead, so the report still shows what
    *would* have been liked). The drafter IS still called under a dry run
    so the preview shows the comment that would have been posted. Counters
    (`likes_attempted`, `comments_attempted`) report the would-be totals.

    An optional `deadline` (see `lib/engagement/scan_deadline.py`) bounds the
    pass in wall-clock time. It is checked at the two safe checkpoints -- the
    start of a source and the start of a post -- never mid-action, so a post
    is always finished before the loop unwinds. The session teardown, the
    funnel and the report all still happen; `stopped_reason` on the report is
    what tells a truncated pass apart from a complete one.
    """
    del now_iso  # interface stability; unused since the queue stage retired
    platform = adapter.platform
    commenter = _resolve_commenter(adapter, drafter, log, inline_comment=inline_comment)
    counters = _Counters(platform)

    with adapter.session():
        # Materialized once: the total is what makes a truncated pass legible
        # in the summary ("12 of 19"), and `list_sources()` is a CSV read plus
        # a cadence filter, not something to re-evaluate mid-loop.
        sources = list(adapter.list_sources())
        counters.sources_total = len(sources)
        for source in sources:
            if _deadline_passed(deadline, counters, log):
                break
            if not gate_source(platform, rate_tracker, log):
                counters.stopped_reason = STOPPED_RATE_LIMIT
                break
            counters.sources_visited += 1
            for post in adapter.iterate_posts(source):
                if _deadline_passed(deadline, counters, log):
                    break
                counters.add(
                    post,
                    process_post(
                        post=post,
                        source=source,
                        adapter=adapter,
                        policy=policy,
                        dedup=dedup,
                        rate_tracker=rate_tracker,
                        log=log,
                        score_relevance=score_relevance,
                        dry_run=dry_run,
                        commenter=commenter,
                        drafter=drafter,
                        comment_gate=comment_gate,
                    ),
                )
            if counters.stopped_reason is not None:
                break
            _pace_between_sources(platform, rate_tracker)

    return counters.to_report()


def _deadline_passed(deadline: ScanDeadline | None, counters: _Counters, log: _Log) -> bool:
    """True when the pass must stop here; records the reason the first time.

    Logged as a warning rather than info: a run that ran out of budget did
    less than it was asked to, and the operator has to be able to find that
    without reading the funnel arithmetic.
    """
    if deadline is None or not deadline.expired():
        return False
    if counters.stopped_reason is None:
        counters.stopped_reason = STOPPED_DEADLINE
        log.warning(
            "scan_stopped_at_deadline platform=%s sources_visited=%d sources_total=%d "
            "posts_scanned=%d budget=%.0fs reserve=%.0fs",
            counters.platform,
            counters.sources_visited,
            counters.sources_total,
            counters.posts_scanned,
            deadline.budget_seconds,
            deadline.reserve_seconds,
        )
    return True


def _resolve_commenter(
    adapter: OutboundAdapter,
    drafter: _Drafter | None,
    log: _Log,
    *,
    inline_comment: bool,
) -> SupportsComment | None:
    """Probe the adapter's inline-comment capability, warning once if absent."""
    if not inline_comment:
        return None
    if isinstance(adapter, SupportsComment) and drafter is not None:
        return adapter
    log.warning(
        "inline_comment_unavailable platform=%s supports_comment=%s "
        "drafter=%s (scan degrades to like-only)",
        adapter.platform,
        isinstance(adapter, SupportsComment),
        drafter is not None,
    )
    return None


def _pace_between_sources(platform: str, rate_tracker: _RateTracker) -> None:
    """Human-cadence pause between Facebook group visits."""
    if platform == "facebook" and rate_tracker.can_act(platform, "group_visit"):
        rate_tracker.wait_random_delay(platform, "group_visit")


class _Counters:
    """Mutable running totals for one scan, folded into a `ScanReport`."""

    def __init__(self, platform: str) -> None:
        self.platform = platform
        self.candidate_count = 0
        self.sources_visited = 0
        self.sources_total = 0
        self.stopped_reason: str | None = None
        self.posts_scanned = 0
        self.likes_attempted = 0
        self.likes_succeeded = 0
        self.comments_attempted = 0
        self.comments_posted = 0
        self.comments_declined = 0
        self.duplicates = 0
        self.extraction_failed = 0
        self.empty_caption = 0
        self.scored_below_threshold = 0
        self.pre_filtered: dict[str, int] = {}
        self.pre_filtered_posts: list[tuple[str, str]] = []

    def add(self, post: Post, outcome: PostOutcome) -> None:
        """Fold one post's outcome into the running totals."""
        self.posts_scanned += 1
        # Scrape health is a property of the POST, not of the gate it left
        # through, so it is counted before any early return -- a run whose
        # extraction broke must report that even if every post was a duplicate.
        self._add_extraction(post)
        self.duplicates += int(outcome.duplicate)
        self.scored_below_threshold += int(outcome.scored_below_threshold)
        if outcome.pre_filter_reason is not None:
            self._add_pre_filtered(post, outcome.pre_filter_reason)
            return
        self.likes_attempted += int(outcome.like_attempted)
        self.likes_succeeded += int(outcome.like_succeeded)
        self.comments_attempted += int(outcome.comment_attempted)
        self.comments_posted += int(outcome.comment_posted)
        self.comments_declined += int(outcome.comment_declined)
        if outcome.candidate_score is not None:
            self.candidate_count += 1

    def _add_extraction(self, post: Post) -> None:
        """Count the adapter's self-reported scrape outcome for one post."""
        status = extraction_status(post)
        if status == EXTRACTION_FAILED:
            self.extraction_failed += 1
        elif status == EXTRACTION_EMPTY_CAPTION:
            self.empty_caption += 1

    def _add_pre_filtered(self, post: Post, reason: str) -> None:
        """Record one adapter rejection, by reason and by post."""
        self.pre_filtered[reason] = self.pre_filtered.get(reason, 0) + 1
        self.pre_filtered_posts.append((post.post_id, reason))

    def to_report(self) -> ScanReport:
        """Freeze the running totals into the scan's public result.

        `queued` is hardwired to 0: the two-stage queue is retired but the
        report shape (`scan_results.ScanReport`) is unchanged.
        """
        return ScanReport(
            platform=self.platform,
            sources_visited=self.sources_visited,
            sources_total=self.sources_total,
            stopped_reason=self.stopped_reason,
            posts_scanned=self.posts_scanned,
            candidates=self.candidate_count,
            likes_attempted=self.likes_attempted,
            likes_succeeded=self.likes_succeeded,
            queued=0,
            pre_filtered=self.pre_filtered,
            pre_filtered_posts=self.pre_filtered_posts,
            comments_attempted=self.comments_attempted,
            comments_posted=self.comments_posted,
            comments_declined=self.comments_declined,
            duplicates=self.duplicates,
            extraction_failed=self.extraction_failed,
            empty_caption=self.empty_caption,
            scored_below_threshold=self.scored_below_threshold,
        )
