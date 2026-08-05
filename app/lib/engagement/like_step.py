"""The like step of a post visit: send the like, spend the budget, log it.

Counterpart to `lib/engagement/inline_comment.py`. Both platforms use this;
only Instagram has a non-zero like quota today.

Split out of `post_processor.py` to keep every engagement module under the
300-line cap.
"""

from __future__ import annotations

from lib.engagement.adapter import OutboundAdapter, Source
from lib.engagement.collaborators import Dedup, Log, RateTracker
from lib.engagement.policy import EngagementPolicy
from lib.engagement.post import Post
from lib.engagement.result import LikeResult
from lib.engagement.scan_results import LikeOutcome


def run_like_step(
    *,
    post: Post,
    source: Source,
    adapter: OutboundAdapter,
    policy: EngagementPolicy,
    dedup: Dedup,
    rate_tracker: RateTracker,
    log: Log,
    dry_run: bool,
) -> LikeOutcome:
    """Like the post if the platform has a like quota and budget remains.

    Quota-gate before rate-tracker probe: `rate_limiter.DAILY_LIMITS` has no
    `facebook:like` key (FB doesn't like inline today) and `can_act` raises
    on unknown keys. Skip both for platforms with daily_like_quota == 0.
    """
    platform = adapter.platform
    if policy.daily_like_quota.get(platform, 0) <= 0:
        return LikeOutcome()
    if not rate_tracker.can_act(platform, "like"):
        return LikeOutcome()

    result = _perform_like(post, adapter, log, dry_run)
    if result.liked:
        _record_like(post, source, platform, dedup, rate_tracker, log)
        return LikeOutcome(attempted=True, succeeded=True)
    if not dry_run:
        log.info(
            "post_like_failed platform=%s post_id=%s reason=%s url=%s",
            platform,
            post.post_id,
            result.reason,
            post.post_url,
        )
    return LikeOutcome(attempted=True)


def _perform_like(
    post: Post, adapter: OutboundAdapter, log: Log, dry_run: bool
) -> LikeResult:
    """Send the like, or record a would-be like under a dry run."""
    if not dry_run:
        return adapter.like(post)
    log.info(
        "post_like_dry_run platform=%s post_id=%s url=%s (no like sent)",
        adapter.platform,
        post.post_id,
        post.post_url,
    )
    return LikeResult.skipped("dry_run")


def _record_like(
    post: Post,
    source: Source,
    platform: str,
    dedup: Dedup,
    rate_tracker: RateTracker,
    log: Log,
) -> None:
    """Spend the like budget, mark the post engaged, and log the success."""
    rate_tracker.record_action(platform, "like")
    dedup.mark_engaged(platform, post.post_id, "like", source.name or "")
    log.info(
        "post_liked platform=%s post_id=%s url=%s",
        platform,
        post.post_id,
        post.post_url,
    )
