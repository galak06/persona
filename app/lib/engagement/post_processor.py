"""Per-post processing: dedup gate, score, like, comment, mark seen.

One `process_post` call is one post visit. The orchestrator
(`lib/engagement/pipeline.py`) owns the loop; this module owns what happens
to a single post inside it.

Split out of `pipeline.py` to keep every engagement module under the
300-line cap.
"""

from __future__ import annotations

from collections.abc import Callable

from lib.engagement.adapter import OutboundAdapter, Source, SupportsComment
from lib.engagement.collaborators import (
    Dedup,
    Drafter,
    Log,
    RateTracker,
    SupportsMarkSeen,
)
from lib.engagement.inline_comment import maybe_comment
from lib.engagement.like_step import run_like_step
from lib.engagement.policy import EngagementPolicy
from lib.engagement.post import Post
from lib.engagement.scan_results import CommentOutcome, PostOutcome


def gate_source(platform: str, rate_tracker: RateTracker, log: Log) -> bool:
    """Per-source rate-limit gate. FB records a `group_visit`; IG is free."""
    if platform != "facebook":
        return True
    if not rate_tracker.can_act(platform, "group_visit"):
        log.info("rate_limit_exhausted platform=%s action=group_visit", platform)
        return False
    rate_tracker.record_action(platform, "group_visit")
    return True


def process_post(
    *,
    post: Post,
    source: Source,
    adapter: OutboundAdapter,
    policy: EngagementPolicy,
    dedup: Dedup,
    rate_tracker: RateTracker,
    log: Log,
    score_relevance: Callable[[Post], float],
    dry_run: bool = False,
    commenter: SupportsComment | None = None,
    drafter: Drafter | None = None,
) -> PostOutcome:
    """Score, like, optionally comment, and mark one post."""
    platform = adapter.platform
    _log_scanned(post, source, platform, log)
    if dedup.is_duplicate(platform, post.post_id):
        return PostOutcome()

    outcome = _visit_post(
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
    )
    # Iterate-once, marked AFTER the visit so a failed comment stays
    # retryable (see `PostOutcome.is_retryable`). The tradeoff: a crash
    # mid-run now costs re-visits next run rather than silently burning
    # posts we never actually engaged with. Re-visiting is the cheap,
    # self-correcting direction; a permanently skipped post is not.
    #
    # Withholding the mark only restores eligibility if nothing ELSE marks
    # the post, so the collaborator's duplicate gate has to agree. IG's
    # `lib.scan_dedup.ScanDedup` asks `already_commented` rather than the
    # presence-only `deduplication.is_duplicate` — otherwise the like this
    # visit just recorded would make the post a duplicate forever and the
    # retry below would never happen. Facebook keeps presence-only semantics
    # via the bare `deduplication` module, which is correct for its
    # two-stage flow.
    if not outcome.is_retryable:
        mark_seen(dedup, platform, post.post_id, log=log, dry_run=dry_run)
    return outcome


def _visit_post(
    *,
    post: Post,
    source: Source,
    adapter: OutboundAdapter,
    policy: EngagementPolicy,
    dedup: Dedup,
    rate_tracker: RateTracker,
    log: Log,
    score_relevance: Callable[[Post], float],
    dry_run: bool,
    commenter: SupportsComment | None,
    drafter: Drafter | None,
) -> PostOutcome:
    """Filter, score, like and comment one non-duplicate post."""
    platform = adapter.platform
    reason = adapter.pre_filter(post)
    if reason is not None:
        return PostOutcome(pre_filter_reason=reason)

    score = adapter.adjust_score(post, score_relevance(post))
    if not policy.is_candidate(score):
        return PostOutcome()

    like = run_like_step(
        post=post,
        source=source,
        adapter=adapter,
        policy=policy,
        dedup=dedup,
        rate_tracker=rate_tracker,
        log=log,
        dry_run=dry_run,
    )
    comment, candidate_score = _run_comment_step(
        post=post,
        source=source,
        platform=platform,
        score=score,
        policy=policy,
        dedup=dedup,
        rate_tracker=rate_tracker,
        log=log,
        dry_run=dry_run,
        commenter=commenter,
        drafter=drafter,
    )
    return PostOutcome(
        like_attempted=like.attempted,
        like_succeeded=like.succeeded,
        candidate_score=candidate_score,
        comment_attempted=comment.attempted,
        comment_posted=comment.posted,
        comment_declined=comment.declined,
        comment_failed=comment.failed,
    )


def _run_comment_step(
    *,
    post: Post,
    source: Source,
    platform: str,
    score: float,
    policy: EngagementPolicy,
    dedup: Dedup,
    rate_tracker: RateTracker,
    log: Log,
    dry_run: bool,
    commenter: SupportsComment | None,
    drafter: Drafter | None,
) -> tuple[CommentOutcome, float | None]:
    """Comment inline if the post qualifies; return the outcome + its score.

    The returned score is non-None only for comment candidates — that is
    what the two-stage path later cherry-picks from.
    """
    if not _is_comment_candidate(platform, post, score, policy):
        _log_near_miss(post, platform, score, log)
        return CommentOutcome(), None

    log.info(
        "post_candidate platform=%s post_id=%s score=%.2f url=%s",
        platform,
        post.post_id,
        score,
        post.post_url,
    )
    if commenter is None or drafter is None:
        return CommentOutcome(), score

    outcome = maybe_comment(
        post=post,
        source=source,
        platform=platform,
        score=score,
        policy=policy,
        commenter=commenter,
        drafter=drafter,
        dedup=dedup,
        rate_tracker=rate_tracker,
        log=log,
        dry_run=dry_run,
    )
    return outcome, score


def _log_scanned(post: Post, source: Source, platform: str, log: Log) -> None:
    """Log that this post was enumerated, before any gate runs."""
    log.info(
        "post_scanned platform=%s post_id=%s source=%s url=%s",
        platform,
        post.post_id,
        source.name or "",
        post.post_url,
    )


def _log_near_miss(post: Post, platform: str, score: float, log: Log) -> None:
    """Log near-miss posts so users can see why they were skipped."""
    if score < 0.5:
        return
    log.info(
        "post_skipped platform=%s post_id=%s score=%.2f url=%s",
        platform,
        post.post_id,
        score,
        post.post_url,
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


def mark_seen(
    dedup: Dedup,
    platform: str,
    post_id: str,
    *,
    log: Log,
    dry_run: bool,
) -> None:
    """Record that `post_id` was opened, if the dedup collaborator supports it.

    No-op for collaborators without `mark_seen` (the bare `deduplication`
    module Facebook passes), so the two-stage path is untouched. Fully
    suppressed under `dry_run` — a preview must leave posts eligible.
    """
    if dry_run or not isinstance(dedup, SupportsMarkSeen):
        return
    dedup.mark_seen(platform, post_id)
    log.info("post_marked_seen platform=%s post_id=%s", platform, post_id)
