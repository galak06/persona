"""Data contracts for the engagements DB layer.

The repository works on plain dicts (like ``groups_db``). ``ENGAGEMENT_COLUMNS``
defines the typed columns; ``dedup_id`` builds the stable primary key so the same
publish (a comment on a post, a recipe shared to a group) upserts instead of
duplicating across retries.
"""

from __future__ import annotations

import re


class Platform:
    """Allowed values for ``engagements.platform``."""

    FACEBOOK: str = "facebook"
    INSTAGRAM: str = "instagram"
    WORDPRESS: str = "wordpress"

    ALL: frozenset[str] = frozenset({"facebook", "instagram", "wordpress"})


class EngagementKind:
    """Allowed values for ``engagements.kind``."""

    COMMENT: str = "comment"          # outbound comment on someone else's post
    LINK_POST: str = "link_post"      # link-share to a FB group
    FEED_POST: str = "feed_post"      # IG feed post / carousel
    REEL: str = "reel"                # IG / FB reel
    PAGE_POST: str = "page_post"      # FB page post

    ALL: frozenset[str] = frozenset(
        {"comment", "link_post", "feed_post", "reel", "page_post"}
    )


class EngagementStatus:
    """Allowed values for ``engagements.status``."""

    POSTED: str = "posted"
    FAILED: str = "failed"

    ALL: frozenset[str] = frozenset({"posted", "failed"})


def slugify(text: str) -> str:
    """Lowercase, hyphenate, strip non-alphanumerics."""
    return re.sub(r"[^a-z0-9]+", "-", str(text).strip().lower()).strip("-")


def dedup_id(platform: str, kind: str, ref: str) -> str:
    """Stable PK for one publish: ``slug({platform}:{kind}:{ref})``.

    ``ref`` is the natural key the caller controls — the third-party post id for a
    comment, or ``{group}:{recipe}`` / a permalink for a post. Same ref upserts.
    """
    return slugify(f"{platform}:{kind}:{ref}") or slugify(f"{platform}:{kind}")


# Dict keys that map to typed columns (everything the repository reads/writes).
ENGAGEMENT_COLUMNS: tuple[str, ...] = (
    "platform",
    "kind",
    "status",
    "target_name",
    "target_url",
    "permalink",
    "content",
    "source_ref",
    "error",
    "posted_at",
)
