"""Per-post log lines emitted while a scan walks its posts.

Extracted from `post_processor.py` (264 lines, against a 300-line cap) when
`post_scored` was added: the module owns what happens to a post, this one owns
what gets said about it. Same `Log` protocol, same event-name-first
printf-style shape the rest of the pipeline uses (`lib/bootstrap.py` hands the
flows a stdlib `logging.Logger`, which takes positional args, not kwargs).

`post_scored` is deliberately INFO rather than DEBUG. `lib.bootstrap.init_script`
calls `configure_logging()` with no level, hardwiring INFO for every flow, so a
DEBUG line would never reach production logs -- and a diagnostic that is
invisible exactly when it is needed is the failure this instrumentation exists
to prevent. The cost is one line per post, the same cadence `post_scanned`
already emits, so it changes the log volume by a constant factor rather than an
order of magnitude.
"""

from __future__ import annotations

from lib.engagement.adapter import Source
from lib.engagement.collaborators import Log
from lib.engagement.post import Post


def log_scanned(post: Post, source: Source, platform: str, log: Log) -> None:
    """Log that this post was enumerated, before any gate runs."""
    log.info(
        "post_scanned platform=%s post_id=%s source=%s url=%s",
        platform,
        post.post_id,
        source.name or "",
        post.post_url,
    )


def log_scored(post: Post, platform: str, score: float, log: Log) -> None:
    """Log the score of EVERY scored post, with the caption length beside it.

    The caption length is the tell that makes a score readable: 0.15 with
    `caption_len=0` is a scrape that failed, 0.15 with `caption_len=400` is
    genuinely irrelevant content. Without it the two are indistinguishable,
    which is how a total extraction regression stayed invisible for a week.
    """
    log.info(
        "post_scored platform=%s post_id=%s score=%.2f caption_len=%d url=%s",
        platform,
        post.post_id,
        score,
        len(post.text),
        post.post_url,
    )


def log_near_miss(post: Post, platform: str, score: float, log: Log) -> None:
    """Log near-miss posts so users can see why they were skipped.

    Kept at its historical 0.5 floor: `post_scored` above now covers the whole
    distribution, so this line stays what it always was -- the shortlist of
    posts that came close.
    """
    if score < 0.5:
        return
    log.info(
        "post_skipped platform=%s post_id=%s score=%.2f url=%s",
        platform,
        post.post_id,
        score,
        post.post_url,
    )
