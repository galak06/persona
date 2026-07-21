"""Two-stage cherry-pick + queue write (Facebook only).

The scan collects candidates, then hands the top-N of them to a later
commenter stage (`scripts/fb_comment.py`) through `QueueIO`. Skipped
entirely in single-pass mode, where the comment happens in-visit — see
`lib/engagement/inline_comment.py`.

Split out of `pipeline.py` to keep every engagement module under the
300-line cap.
"""

from __future__ import annotations

from collections.abc import Callable

from lib.engagement.collaborators import Drafter, Log, QueueIO
from lib.engagement.log import log_engagement
from lib.engagement.policy import EngagementPolicy
from lib.engagement.post import Post


def cherry_pick_and_queue(
    *,
    platform: str,
    candidates: list[tuple[Post, float]],
    policy: EngagementPolicy,
    drafter: Drafter | None,
    queue_io: QueueIO,
    now_iso: Callable[[], str],
    log: Log,
    dry_run: bool = False,
) -> int:
    """Sort by score desc, take top-N within today's quota, draft + queue.

    When ``drafter`` is ``None`` the scan only enqueues the target post with an
    empty ``draft_comment`` (scan-only mode); drafting happens later, at post
    time, in the platform's dedicated commenter (e.g. ``scripts/fb_comment.py``).

    When ``dry_run`` is True the selected posts are logged but never drafted
    (no LLM call) and never appended to ``queue_io``. The returned count is
    what a live run would have queued.
    """
    selected = _select_within_budget(platform, candidates, policy, queue_io)
    queued = 0
    for post, score in selected:
        if dry_run:
            _log_dry_run(post, platform, score, log)
        else:
            _queue_one(
                post=post,
                platform=platform,
                score=score,
                policy=policy,
                drafter=drafter,
                queue_io=queue_io,
                now_iso=now_iso,
                log=log,
            )
        queued += 1
    return queued


def _select_within_budget(
    platform: str,
    candidates: list[tuple[Post, float]],
    policy: EngagementPolicy,
    queue_io: QueueIO,
) -> list[tuple[Post, float]]:
    """Highest-scoring candidates that fit in today's remaining quota."""
    quota = policy.daily_comment_quota.get(platform, 0)
    budget = max(0, quota - queue_io.existing_today(platform))
    if budget == 0:
        return []
    return sorted(candidates, key=lambda c: c[1], reverse=True)[:budget]


def _queue_one(
    *,
    post: Post,
    platform: str,
    score: float,
    policy: EngagementPolicy,
    drafter: Drafter | None,
    queue_io: QueueIO,
    now_iso: Callable[[], str],
    log: Log,
) -> None:
    """Draft (if a drafter is wired), append to the queue, and log it."""
    draft = _draft_for(post, platform, drafter, log)
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


def _draft_for(
    post: Post, platform: str, drafter: Drafter | None, log: Log
) -> str:
    """Comment text for this queue record; "" in scan-only mode."""
    if drafter is None:
        return ""
    draft = drafter.draft_comment_for_post(
        platform=platform,
        post_text=post.text,
        group_or_hashtag=post.source_name,
        post_url=post.post_url,
    )
    if not draft:
        log.info(
            "draft_inline_empty platform=%s post_url=%s",
            platform,
            post.post_url,
        )
    return draft


def _log_dry_run(post: Post, platform: str, score: float, log: Log) -> None:
    """Report a post that a live run would have queued."""
    log.info(
        "post_queue_dry_run platform=%s post_id=%s score=%.2f url=%s "
        "(not queued)",
        platform,
        post.post_id,
        score,
        post.post_url,
    )
