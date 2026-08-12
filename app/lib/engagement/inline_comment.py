"""The inline (single-pass) comment step: draft and post in one visit.

Both engagers run this path via `run_outbound_scan(inline_comment=True)`:
`scripts/ig_engager.py` and `scripts/fb_engager.py` each like AND comment
a qualifying post in the single visit that opened it. The old Facebook
two-stage queue (scan -> queue file -> separate commenter run) is retired.

Every posted or failed comment is persisted to `lib.engagements_db`
(`record_publish` swallows DB errors, so recording can never break a run)
and to the JSONL engagement log with the canonical `comment` action.

Split out of `pipeline.py` to keep every engagement module under the
300-line cap.
"""

from __future__ import annotations

from datetime import UTC, datetime

from lib import engagements_db
from lib.engagement.adapter import Source, SupportsComment
from lib.engagement.collaborators import CommentGate, Dedup, Drafter, Log, RateTracker
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
    comment_gate: CommentGate | None = None,
) -> CommentOutcome:
    """Draft and post one comment during this post's visit.

    Order: auto-approve gate -> comment gate -> comment quota -> draft ->
    post -> record. Under `dry_run` the drafter still runs (so the preview
    shows the real text) but nothing leaves the process: no `comment()`,
    no rate spend, no dedup mark.
    """
    if _blocked_by_approval_gate(post, score, platform, policy, log):
        return CommentOutcome()
    if _blocked_by_comment_gate(post, source, platform, comment_gate, log):
        return CommentOutcome()
    if _blocked_by_comment_quota(platform, rate_tracker, log):
        return CommentOutcome()

    text = _draft(post, platform, drafter)
    if not text:
        _log_decline(post, platform, score, log, drafter)
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
        "comment_skipped_needs_approval platform=%s post_id=%s score=%.2f threshold=%.2f url=%s",
        platform,
        post.post_id,
        score,
        policy.approval_threshold,
        post.post_url,
    )
    return True


def _blocked_by_comment_gate(
    post: Post,
    source: Source,
    platform: str,
    comment_gate: CommentGate | None,
    log: Log,
) -> bool:
    """True when the injected `CommentGate` vetoes this post's comment.

    Runs after the approval band (so borderline posts never trigger gate
    side effects like first-comment flagging) and before the quota/LLM
    steps (a vetoed post must spend nothing). A gated skip is terminal,
    not retryable: the post is still liked upstream and gets marked seen,
    so it is never revisited even after the gate later opens.
    """
    if comment_gate is None:
        return False
    reason = comment_gate.check(post, source)
    if reason is None:
        return False
    log.info(
        "comment_skipped_gated platform=%s post_id=%s reason=%s url=%s",
        platform,
        post.post_id,
        reason,
        post.post_url,
    )
    return True


def _blocked_by_comment_quota(platform: str, rate_tracker: RateTracker, log: Log) -> bool:
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


def _log_decline(
    post: Post, platform: str, score: float, log: Log, drafter: Drafter | None = None
) -> None:
    """Record why this post got no comment.

    The drafter is agentic and returns "" for several very different reasons:
    a genuine `engage:false` editorial decision, an upstream call that failed,
    a blank comment, or two voice-validation failures. This used to log all of
    them as `agent_declined_or_empty_draft`, so a total outage looked exactly
    like the agent being selective — which is how a retired model went
    unnoticed for a full run. Drafters that track it expose `last_outcome`;
    anything else (fakes, other implementations) falls back to the old label.
    """
    reason = getattr(drafter, "last_outcome", None) or "agent_declined_or_empty_draft"
    log.info(
        "comment_declined platform=%s post_id=%s score=%.2f reason=%s url=%s",
        platform,
        post.post_id,
        score,
        reason,
        post.post_url,
    )


def _log_dry_run(post: Post, platform: str, score: float, text: str, log: Log) -> None:
    """Show the comment a live run would have posted, without posting it."""
    log.info(
        "post_comment_dry_run platform=%s post_id=%s score=%.2f url=%s draft=%r (no comment sent)",
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
        engagements_db.record_publish(
            platform=platform,
            kind="comment",
            status="failed",
            target_name=post.source_name or "",
            target_url=post.post_url,
            content=text,
            ref=post.post_id,
            error=result.reason,
        )
        # `failed` (not merely "not posted") keeps the post retryable — see
        # `PostOutcome.is_retryable`; it must NOT be marked seen.
        return CommentOutcome(attempted=True, failed=True)

    _record_comment(
        post=post,
        source=source,
        platform=platform,
        score=score,
        text=text,
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
    text: str,
    dedup: Dedup,
    rate_tracker: RateTracker,
    log: Log,
) -> None:
    """Spend the budget, mark engaged, persist (JSONL + DB), pace the next one."""
    rate_tracker.record_action(platform, "comment")
    dedup.mark_engaged(platform, post.post_id, "comment", source.name or "")
    log_engagement(
        "comment",
        platform,
        post.source_name or source.name or "",
        text,
        post_url=post.post_url,
        post_id=post.post_id,
        relevance_score=round(score, 3),
        post_text=post.text,
    )
    engagements_db.record_publish(
        platform=platform,
        kind="comment",
        status="posted",
        target_name=post.source_name or "",
        target_url=post.post_url,
        content=text,
        ref=post.post_id,
        posted_at=datetime.now(UTC).isoformat(),
    )
    log.info(
        "post_commented platform=%s post_id=%s score=%.2f url=%s",
        platform,
        post.post_id,
        score,
        post.post_url,
    )
    rate_tracker.wait_random_delay(platform, "comment")
