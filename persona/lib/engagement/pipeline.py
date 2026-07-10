"""Pipeline for OutboundEngagement.

`run_outbound_scan` glues an `OutboundAdapter` to platform-agnostic
collaborators (dedup, rate tracker, drafter, queue, log) and returns a
`ScanReport`. Cherry-picks the top-N candidates per platform per day,
where N is the remaining `EngagementPolicy.daily_comment_quota` budget
after subtracting records already queued today. Both `scripts/fb_scan.py`
and `scripts/ig_scan.py` are thin wrappers around it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from lib.engagement.adapter import OutboundAdapter, Source
from lib.engagement.log import log_engagement
from lib.engagement.policy import EngagementPolicy
from lib.engagement.post import Post


@dataclass(frozen=True)
class ScanReport:
    """Aggregated counters from one `run_outbound_scan` invocation.

    `pre_filtered` maps adapter rejection reason (e.g. "competitor",
    "own_account", "too_old") to count of posts dropped for that reason.
    `pre_filtered_posts` lists the (post_id, reason) pairs for those drops so
    callers can act on individual posts (e.g. permanently dedup-mark them).
    """

    platform: str
    sources_visited: int
    posts_scanned: int
    candidates: int
    likes_attempted: int
    likes_succeeded: int
    queued: int
    pre_filtered: dict[str, int] = field(default_factory=dict)
    # (post_id, reason) pairs for each pre-filtered drop.
    pre_filtered_posts: list[tuple[str, str]] = field(default_factory=list)


# Structural collaborator protocols so production singleton modules satisfy
# the shape without wrapping (rate_limiter, deduplication, draft_helper).


class _Dedup(Protocol):
    def is_duplicate(self, platform: str, post_id: str) -> bool: ...
    def mark_engaged(
        self,
        platform: str,
        post_id: str,
        action: str,
        group_or_hashtag: str = ...,
        status: str = ...,
    ) -> None: ...


class _RateTracker(Protocol):
    def can_act(self, platform: str, action: str) -> bool: ...
    def record_action(self, platform: str, action: str) -> int: ...
    def wait_random_delay(self, platform: str, action: str) -> None: ...


class _Drafter(Protocol):
    def draft_comment_for_post(
        self,
        *,
        platform: str,
        post_text: str,
        group_or_hashtag: str | None,
        post_url: str,
    ) -> str: ...


class _QueueIO(Protocol):
    def append(self, record: dict[str, object]) -> None: ...
    def save(self) -> None: ...
    def existing_today(self, platform: str) -> int: ...


class _Log(Protocol):
    def info(self, msg: str, /, *args: object, **kwargs: object) -> None: ...
    def warning(self, msg: str, /, *args: object, **kwargs: object) -> None: ...


def run_outbound_scan(
    adapter: OutboundAdapter,
    policy: EngagementPolicy,
    *,
    dedup: _Dedup,
    rate_tracker: _RateTracker,
    drafter: _Drafter | None,
    queue_io: _QueueIO,
    log: _Log,
    now_iso: Callable[[], str],
    score_relevance: Callable[[Post], float],
) -> ScanReport:
    """Run one outbound-engagement scan and return a `ScanReport`.

    The adapter owns platform mechanics (session, source enumeration, post
    extraction, pre-filter, score adjustment, inline like). The pipeline
    owns orchestration: dedup gating, scoring, like rate limits, candidate
    collection, cherry-pick, draft, queue append, persist.
    """
    platform = adapter.platform
    candidates: list[tuple[Post, float]] = []
    pre_filtered: dict[str, int] = {}
    pre_filtered_posts: list[tuple[str, str]] = []
    sources_visited = 0
    posts_scanned = 0
    likes_attempted = 0
    likes_succeeded = 0

    with adapter.session():
        for source in adapter.list_sources():
            if not _gate_source(platform, rate_tracker, log):
                break
            sources_visited += 1

            for post in adapter.iterate_posts(source):
                posts_scanned += 1
                outcome = _process_post(
                    post=post,
                    source=source,
                    adapter=adapter,
                    policy=policy,
                    dedup=dedup,
                    rate_tracker=rate_tracker,
                    log=log,
                    score_relevance=score_relevance,
                )
                if outcome.pre_filter_reason is not None:
                    reason = outcome.pre_filter_reason
                    pre_filtered[reason] = pre_filtered.get(reason, 0) + 1
                    pre_filtered_posts.append((post.post_id, reason))
                    continue
                if outcome.like_attempted:
                    likes_attempted += 1
                if outcome.like_succeeded:
                    likes_succeeded += 1
                if outcome.candidate_score is not None:
                    candidates.append((post, outcome.candidate_score))

            if platform == "facebook" and rate_tracker.can_act(
                platform, "group_visit"
            ):
                rate_tracker.wait_random_delay(platform, "group_visit")

        queued = _cherry_pick_and_queue(
            platform=platform,
            candidates=candidates,
            policy=policy,
            drafter=drafter,
            queue_io=queue_io,
            now_iso=now_iso,
            log=log,
        )
        queue_io.save()

    return ScanReport(
        platform=platform,
        sources_visited=sources_visited,
        posts_scanned=posts_scanned,
        candidates=len(candidates),
        likes_attempted=likes_attempted,
        likes_succeeded=likes_succeeded,
        queued=queued,
        pre_filtered=pre_filtered,
        pre_filtered_posts=pre_filtered_posts,
    )


# --- Internal helpers -------------------------------------------------------


@dataclass(frozen=True)
class _PostOutcome:
    pre_filter_reason: str | None = None
    like_attempted: bool = False
    like_succeeded: bool = False
    candidate_score: float | None = None


def _gate_source(platform: str, rate_tracker: _RateTracker, log: _Log) -> bool:
    """Per-source rate-limit gate. FB records a `group_visit`; IG is free."""
    if platform != "facebook":
        return True
    if not rate_tracker.can_act(platform, "group_visit"):
        log.info("rate_limit_exhausted platform=%s action=group_visit", platform)
        return False
    rate_tracker.record_action(platform, "group_visit")
    return True


def _process_post(
    *,
    post: Post,
    source: Source,
    adapter: OutboundAdapter,
    policy: EngagementPolicy,
    dedup: _Dedup,
    rate_tracker: _RateTracker,
    log: _Log,
    score_relevance: Callable[[Post], float],
) -> _PostOutcome:
    """Score, like, and mark one post. Returns the per-post counters."""
    platform = adapter.platform
    if dedup.is_duplicate(platform, post.post_id):
        return _PostOutcome()

    reason = adapter.pre_filter(post)
    if reason is not None:
        return _PostOutcome(pre_filter_reason=reason)

    base = score_relevance(post)
    score = adapter.adjust_score(post, base)
    if not policy.is_candidate(score):
        return _PostOutcome()

    like_attempted = False
    like_succeeded = False
    # Quota-gate before rate-tracker probe: `rate_limiter.DAILY_LIMITS` has no
    # `facebook:like` key (FB doesn't like inline today) and `can_act` raises
    # on unknown keys. Skip both for platforms with daily_like_quota == 0.
    if policy.daily_like_quota.get(platform, 0) > 0 and rate_tracker.can_act(
        platform, "like"
    ):
        like_attempted = True
        result = adapter.like(post)
        if result.liked:
            like_succeeded = True
            rate_tracker.record_action(platform, "like")
            dedup.mark_engaged(
                platform, post.post_id, "like", source.name or ""
            )

    candidate_score: float | None = None
    if _is_comment_candidate(platform, post, score, policy):
        candidate_score = score
        log.info(
            "post_candidate platform=%s post_id=%s score=%.2f url=%s",
            platform,
            post.post_id,
            score,
            post.post_url,
        )
    elif score >= 0.5:
        # Log near-misses at info level so users can see why posts were skipped.
        log.info(
            "post_skipped platform=%s post_id=%s score=%.2f url=%s",
            platform,
            post.post_id,
            score,
            post.post_url,
        )

    return _PostOutcome(
        like_attempted=like_attempted,
        like_succeeded=like_succeeded,
        candidate_score=candidate_score,
    )


def _is_comment_candidate(
    platform: str, post: Post, score: float, policy: EngagementPolicy
) -> bool:
    """Platform-specific comment-candidacy gate.

    IG requires '?' in the post text (Nalla's-Dad voice answers genuine
    questions only); FB has no such gate today.
    """
    if not policy.is_comment_candidate(score):
        return False
    if platform == "instagram":
        return "?" in post.text
    return True


def _cherry_pick_and_queue(
    *,
    platform: str,
    candidates: list[tuple[Post, float]],
    policy: EngagementPolicy,
    drafter: _Drafter | None,
    queue_io: _QueueIO,
    now_iso: Callable[[], str],
    log: _Log,
) -> int:
    """Sort by score desc, take top-N within today's quota, draft + queue.

    When ``drafter`` is ``None`` the scan only enqueues the target post with an
    empty ``draft_comment`` (scan-only mode); drafting happens later, at post
    time, in the platform's dedicated commenter (e.g. ``scripts/fb_comment.py``).
    """
    quota = policy.daily_comment_quota.get(platform, 0)
    existing = queue_io.existing_today(platform)
    budget = max(0, quota - existing)
    if budget == 0:
        return 0

    selected = sorted(candidates, key=lambda c: c[1], reverse=True)[:budget]
    queued = 0
    for post, score in selected:
        draft = (
            drafter.draft_comment_for_post(
                platform=platform,
                post_text=post.text,
                group_or_hashtag=post.source_name,
                post_url=post.post_url,
            )
            if drafter is not None
            else ""
        )
        if not draft and drafter is not None:
            log.info(
                "draft_inline_empty platform=%s post_url=%s",
                platform,
                post.post_url,
            )
        record = post.to_queue_record(
            score=score,
            draft=draft,
            requires_approval=(
                policy.requires_approval(score) or platform == "instagram"
            ),
            queued_at=now_iso(),
        )
        queue_io.append(record)
        log_engagement(
            "queued",
            platform,
            post.post_url,
            f"Queued post for commenting: {post.post_url} (score={score:.2f})",
        )
        log.info(
            "post_queued platform=%s post_id=%s score=%.2f url=%s",
            platform,
            post.post_id,
            score,
            post.post_url,
        )
        queued += 1
    return queued
