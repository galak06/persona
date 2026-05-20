"""Normalized Post dataclass for OutboundEngagement.

A Post is a platform-agnostic representation of a discovered third-party post.
OutboundAdapter implementations construct Post instances; the scanner pipeline
consumes them. The to_queue_record method produces the per-platform queue
record shape consumed by comment_poster downstream.
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

    def to_queue_record(
        self,
        *,
        score: float,
        draft: str,
        requires_approval: bool,
        queued_at: str,
    ) -> dict[str, object]:
        """Produce the platform-specific queue record dict.

        FB record shape mirrors scripts/fb_scan.py lines 637-652:
          platform, post_url, post_id, post_text, group_name, group_url,
          category, relevance_score, queued_at, status, requires_approval, draft_comment

        IG record shape mirrors scripts/ig_scan.py lines 580-595:
          platform, post_url, post_id, post_text, hashtag, author,
          category, relevance_score, like_count, queued_at, status, requires_approval, draft_comment
        """
        base: dict[str, object] = {
            "platform": self.platform,
            "post_url": self.post_url,
            "post_id": self.post_id,
            "post_text": self.text,
            "category": self.platform_extra.get("category", ""),
            "relevance_score": round(score, 3),
            "queued_at": queued_at,
            "status": "pending",
            "requires_approval": requires_approval,
            "draft_comment": draft,
        }
        if self.platform == "facebook":
            base["group_name"] = self.source_name or ""
            base["group_url"] = self.source_url or ""
        elif self.platform == "instagram":
            base["hashtag"] = self.source_name or ""
            base["author"] = self.author or ""
            base["like_count"] = self.platform_extra.get("like_count", 0)
        return base
