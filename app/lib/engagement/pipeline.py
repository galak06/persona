"""Pipeline for OutboundEngagement.

`run_outbound_scan` glues an `OutboundAdapter` to platform-agnostic
collaborators (dedup, rate tracker, drafter, queue, log) and returns a
`ScanReport`. Both `scripts/fb_scan.py` and `scripts/ig_scan.py` are thin
wrappers around it, in two different modes:

  - Two-stage (`inline_comment=False`, Facebook): the scan likes and
    cherry-picks the top-N candidates per day into `queue_io`, where N is
    the remaining `EngagementPolicy.daily_comment_quota` budget after
    subtracting records already queued today. A separate commenter stage
    (`scripts/fb_comment.py`) drains the queue and posts.

  - Single-pass (`inline_comment=True`, Instagram): each post is opened
    once and liked AND commented in that same visit, with no queue and no
    handoff. Requires an adapter implementing `SupportsComment` plus a
    `drafter`; `cherry_pick_and_queue` is skipped entirely.

This module owns orchestration only. The collaborator protocols, result
types, per-post processing, the inline comment step and the queue
cherry-pick each live in their own module (the file-size cap is 300
lines); they are re-exported below so existing imports keep working.
"""

from __future__ import annotations

from collections.abc import Callable

from lib.engagement.adapter import OutboundAdapter, SupportsComment
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
    QueueIO as _QueueIO,
)
from lib.engagement.collaborators import (
    RateTracker as _RateTracker,
)
from lib.engagement.collaborators import (
    SupportsMarkSeen as _SupportsMarkSeen,
)
from lib.engagement.policy import EngagementPolicy
from lib.engagement.post import Post
from lib.engagement.post_processor import gate_source, process_post
from lib.engagement.queueing import cherry_pick_and_queue
from lib.engagement.scan_results import PostOutcome, ScanReport

__all__ = [
    "ScanReport",
    "_Dedup",
    "_Drafter",
    "_Log",
    "_QueueIO",
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
    queue_io: _QueueIO | None = None,
    log: _Log,
    now_iso: Callable[[], str],
    score_relevance: Callable[[Post], float],
    dry_run: bool = False,
    inline_comment: bool = False,
) -> ScanReport:
    """Run one outbound-engagement scan and return a `ScanReport`.

    The adapter owns platform mechanics (session, source enumeration, post
    extraction, pre-filter, score adjustment, inline like/comment). The
    pipeline owns orchestration: dedup gating, scoring, rate limits, and
    then EITHER the inline comment (single-pass) or candidate collection +
    cherry-pick + queue append + persist (two-stage).

    When `inline_comment` is True the scan comments during the same visit
    that liked the post, and `cherry_pick_and_queue` is skipped entirely —
    nothing is queued and `queue_io` is unused (and may be None). This
    needs an adapter implementing `SupportsComment` and a non-None
    `drafter`; if either is missing the scan degrades to like-only and logs
    `inline_comment_unavailable` once.

    When `dry_run` is True the scan is read-only: `adapter.like` and
    `adapter.comment` are never called (the post records a
    `LikeResult.skipped("dry_run")` instead, so the report still shows what
    *would* have been liked), nothing is appended to `queue_io` and
    `queue_io.save()` is not called. The drafter IS still called under a dry
    run so the preview shows the comment that would have been posted.
    Counters (`likes_attempted`, `comments_attempted`, `queued`) report the
    would-be totals.
    """
    platform = adapter.platform
    commenter = _resolve_commenter(adapter, drafter, log, inline_comment=inline_comment)
    # Single-pass mode never cherry-picks, so retaining every candidate
    # would build a list nothing ever reads.
    counters = _Counters(platform, collect_candidates=not inline_comment)

    with adapter.session():
        for source in adapter.list_sources():
            if not gate_source(platform, rate_tracker, log):
                break
            counters.sources_visited += 1
            for post in adapter.iterate_posts(source):
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
                    ),
                )
            _pace_between_sources(platform, rate_tracker)

        queued = _drain_to_queue(
            platform=platform,
            candidates=counters.candidates,
            policy=policy,
            drafter=drafter,
            queue_io=queue_io,
            now_iso=now_iso,
            log=log,
            dry_run=dry_run,
            inline_comment=inline_comment,
        )

    return counters.to_report(queued)


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
    supports_comment = isinstance(adapter, SupportsComment)
    if supports_comment and drafter is not None:
        return adapter
    log.warning(
        "inline_comment_unavailable platform=%s supports_comment=%s "
        "drafter=%s (scan degrades to like-only)",
        adapter.platform,
        supports_comment,
        drafter is not None,
    )
    return None


def _pace_between_sources(platform: str, rate_tracker: _RateTracker) -> None:
    """Human-cadence pause between Facebook group visits."""
    if platform == "facebook" and rate_tracker.can_act(platform, "group_visit"):
        rate_tracker.wait_random_delay(platform, "group_visit")


def _drain_to_queue(
    *,
    platform: str,
    candidates: list[tuple[Post, float]],
    policy: EngagementPolicy,
    drafter: _Drafter | None,
    queue_io: _QueueIO | None,
    now_iso: Callable[[], str],
    log: _Log,
    dry_run: bool,
    inline_comment: bool,
) -> int:
    """Cherry-pick candidates into the comment queue and persist it.

    Single-pass mode already commented in-visit: there is no handoff to a
    later commenter stage, so nothing is queued or persisted.
    """
    if inline_comment or queue_io is None:
        return 0
    queued = cherry_pick_and_queue(
        platform=platform,
        candidates=candidates,
        policy=policy,
        drafter=drafter,
        queue_io=queue_io,
        now_iso=now_iso,
        log=log,
        dry_run=dry_run,
    )
    if not dry_run:
        queue_io.save()
    return queued


class _Counters:
    """Mutable running totals for one scan, folded into a `ScanReport`.

    `collect_candidates` controls only whether the (post, score) pairs are
    retained for the cherry-pick — the candidate COUNT is always tracked,
    since the report exposes it in both modes.
    """

    def __init__(self, platform: str, *, collect_candidates: bool) -> None:
        self.platform = platform
        self._collect_candidates = collect_candidates
        self.candidates: list[tuple[Post, float]] = []
        self.candidate_count = 0
        self.sources_visited = 0
        self.posts_scanned = 0
        self.likes_attempted = 0
        self.likes_succeeded = 0
        self.comments_attempted = 0
        self.comments_posted = 0
        self.comments_declined = 0
        self.pre_filtered: dict[str, int] = {}
        self.pre_filtered_posts: list[tuple[str, str]] = []

    def add(self, post: Post, outcome: PostOutcome) -> None:
        """Fold one post's outcome into the running totals."""
        self.posts_scanned += 1
        if outcome.pre_filter_reason is not None:
            self._add_pre_filtered(post, outcome.pre_filter_reason)
            return
        self.likes_attempted += int(outcome.like_attempted)
        self.likes_succeeded += int(outcome.like_succeeded)
        self.comments_attempted += int(outcome.comment_attempted)
        self.comments_posted += int(outcome.comment_posted)
        self.comments_declined += int(outcome.comment_declined)
        if outcome.candidate_score is not None:
            self._add_candidate(post, outcome.candidate_score)

    def _add_pre_filtered(self, post: Post, reason: str) -> None:
        """Record one adapter rejection, by reason and by post."""
        self.pre_filtered[reason] = self.pre_filtered.get(reason, 0) + 1
        self.pre_filtered_posts.append((post.post_id, reason))

    def _add_candidate(self, post: Post, score: float) -> None:
        """Count a comment candidate, retaining it only for the two-stage path."""
        self.candidate_count += 1
        if self._collect_candidates:
            self.candidates.append((post, score))

    def to_report(self, queued: int) -> ScanReport:
        """Freeze the running totals into the scan's public result."""
        return ScanReport(
            platform=self.platform,
            sources_visited=self.sources_visited,
            posts_scanned=self.posts_scanned,
            candidates=self.candidate_count,
            likes_attempted=self.likes_attempted,
            likes_succeeded=self.likes_succeeded,
            queued=queued,
            pre_filtered=self.pre_filtered,
            pre_filtered_posts=self.pre_filtered_posts,
            comments_attempted=self.comments_attempted,
            comments_posted=self.comments_posted,
            comments_declined=self.comments_declined,
        )
