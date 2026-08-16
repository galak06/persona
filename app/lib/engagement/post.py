"""Normalized Post dataclass for OutboundEngagement.

A Post is a platform-agnostic representation of a discovered third-party post.
OutboundAdapter implementations construct Post instances; the scanner pipeline
consumes them.

`to_queue_record` was removed on 2026-08-16: it produced the queue-record shape
`comment_poster` consumed in the two-stage scan-then-drain design that ADR 0002
retired. Both engagers comment inline now, so nothing had called it since --
only its own contract test, which asserted a shape no producer produced.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Post:
    platform: str               # "facebook" | "instagram"
    post_id: str
    post_url: str
    text: str
    author: str | None = None
    source_id: str | None = None      # group_id (FB) or hashtag (IG)
    source_name: str | None = None    # group name or hashtag string
    source_url: str | None = None
    platform_extra: dict[str, object] = field(default_factory=dict)
    # FB extras keys: comment_count, category
    # IG extras keys: like_count, comment_count, weeks_old
