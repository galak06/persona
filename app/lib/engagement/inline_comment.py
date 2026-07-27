"""The inline (single-pass) comment step: draft and post in one visit.

Used only when `run_outbound_scan(inline_comment=True)` (Instagram).
Facebook drafts and posts in a separate stage — see
`lib/engagement/queueing.py` and `scripts/fb_comment.py`.

Split out of `pipeline.py` to keep every engagement module under the
300-line cap.
"""

from __future__ import annotations

from lib.engagement.adapter import Source, SupportsComment
from lib.engagement.collaborators import Dedup, Drafter, Log, RateTracker
from lib.engagement.log import log_engagement
from lib.engagement.policy import EngagementPolicy
from lib.engagement.post import Post
from lib.engagement.scan_results import CommentOutcome


def maybe_comment(
    *,
    post: Post,
    source: Source,
    platform: str,
    score: float,
    policy: EngagementPolicy,
    commenter: SupportsComment,
    drafter: Drafter,
    dedup: Dedup,
    rate_tracker: RateTracker,
    log: Log,
    dry_run: bool,
) -> CommentOutcome:
    """Draft and post one comment during this post's visit.

    Order: auto-approve gate -> comment quota -> draft -> post -> record.
    Under `dry_run` the drafter still runs (so the preview shows the real
    text) but nothing leaves the process: no `comment()`, no rate spend, no
    dedup mark.
    """
    if _blocked_by_approval_gate(post, score, platform, policy, log):
        return CommentOutcome()
    if _blocked_by_comment_quota(platform, rate_tracker, log):
        return CommentOutcome()

    text = _draft(post, platform, drafter)
    if not text:
        _log_decline(post, platform, score, log)
        return CommentOutcome(declined=True)

    if dry_run:
        _log_dry_run(post, platform, score, text, log)
        return CommentOutcome(attempted=True)

    return _submit(
        post=post,
        source=source,
        platform=platform,
        score=score,
        text=text,
        commenter=commenter,
        dedup=dedup,
        rate_tracker=rate_tracker,
        log=log,
    )


def _blocked_by_approval_gate(
    post: Post, score: float, platform: str, policy: EngagementPolicy, log: Log
) -> bool:
    """True if the score falls in the borderline band that needs a human.

    Only the auto-approve tier comments unattended: `requires_approval`
    scores (the 0.75-0.80 band) have no human in this loop, so they are
    skipped rather than posted.
    """
    if not policy.requires_approval(score):
        return False
    log.info(
        "comment_skipped_needs_approval platform=%s post_id=%s score=%.2f "
        "threshold=%.2f url=%s",
        platform,
        post.post_id,
        score,
        policy.approval_threshold,
        post.post_url,
    )
    return True


def _blocked_by_comment_quota(
    platform: str, rate_tracker: RateTracker, log: Log
) -> bool:
    """True if today's comment budget is spent (checked before any LLM call)."""
    if rate_tracker.can_act(platform, "comment"):
        return False
    log.info("rate_limit_exhausted platform=%s action=comment", platform)
    return True


def _draft(post: Post, platform: str, drafter: Drafter) -> str:
    """Ask the agentic drafter for comment text ("" means it declined)."""
    return drafter.draft_comment_for_post(
        platform=platform,
        post_text=post.text,
        group_or_hashtag=post.source_name,
        post_url=post.post_url,
    )


def _log_decline(post: Post, platform: str, score: float, log: Log) -> None:
    """Record that the agent declined to engage with this post.

    The drafter is agentic and returns "" when it declines — that decline
    IS the approval gate, and the model's own `reason` is logged inside
    `lib/draft_helper.py`. The pipeline only sees the empty draft.
    """
    log.info(
        "comment_declined platform=%s post_id=%s score=%.2f "
        "reason=agent_declined_or_empty_draft url=%s",
        platform,
        post.post_id,
        score,
        post.post_url,
    )


def _log_dry_run(
    post: Post, platform: str, score: float, text: str, log: Log
) -> None:
    """Show the comment a live run would have posted, without posting it."""
    log.info(
        "post_comment_dry_run platform=%s post_id=%s score=%.2f url=%s "
        "draft=%r (no comment sent)",
        platform,
        post.post_id,
        score,
        post.post_url,
        text,
    )


def _submit(
    *,
    post: Post,
    source: Source,
    platform: str,
    score: float,
    text: str,
    commenter: SupportsComment,
    dedup: Dedup,
    rate_tracker: RateTracker,
    log: Log,
) -> CommentOutcome:
    """Post the drafted comment and record it, or report a retryable failure."""
    result = commenter.comment(post, text)
    if not result.posted:
        log.warning(
            "post_comment_failed platform=%s post_id=%s reason=%s url=%s",
            platform,
            post.post_id,
            result.reason,
            post.post_url,
        )
        # `failed` (not merely "not posted") keeps the post retryable — see
        # `PostOutcome.is_retryable`; it must NOT be marked seen.
        return CommentOutcome(attempted=True, failed=True)

    _record_comment(
        post=post,
        source=source,
        platform=platform,
        score=score,
        dedup=dedup,
        rate_tracker=rate_tracker,
        log=log,
    )
    return CommentOutcome(attempted=True, posted=True)


def _record_comment(
    *,
    post: Post,
    source: Source,
    platform: str,
    score: float,
    dedup: Dedup,
    rate_tracker: RateTracker,
    log: Log,
) -> None:
    """Spend the comment budget, mark engaged, log, then pace the next one."""
    rate_tracker.record_action(platform, "comment")
    dedup.mark_engaged(platform, post.post_id, "comment", source.name or "")
    log_engagement(
        "commented",
        platform,
        post.post_url,
        f"Commented inline: {post.post_url} (score={score:.2f})",
    )
    log.info(
        "post_commented platform=%s post_id=%s score=%.2f url=%s",
        platform,
        post.post_id,
        score,
        post.post_url,
    )
    rate_tracker.wait_random_delay(platform, "comment")
