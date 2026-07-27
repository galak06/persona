"""Published posts + comments SQLite store (``${BRAND_DIR}/data/db/engagements.db``).

The queryable history behind ``logs/engagement_log.jsonl``. Writers call
``record_publish`` at each publish-success site; the API reads via
``list_engagements`` / ``counts``.

``record_publish`` is **defensive** — it swallows and logs any DB error and
returns ``None`` so a logging failure can never break an actual publish. Use
``EngagementsRepository`` directly when you want errors to surface (tests).
"""

from __future__ import annotations

import logging
from typing import Any

from lib.engagements_db.db import connect, migrate, resolve_engagements_db_path
from lib.engagements_db.models import EngagementKind, EngagementStatus, Platform
from lib.engagements_db.repository import EngagementsRepository

logger = logging.getLogger(__name__)

__all__ = [
    "EngagementKind",
    "EngagementStatus",
    "EngagementsRepository",
    "Platform",
    "connect",
    "counts",
    "list_engagements",
    "migrate",
    "posted_comment_post_ids",
    "record_publish",
    "resolve_engagements_db_path",
]


def _repo() -> EngagementsRepository:
    return EngagementsRepository(None)


def record_publish(
    *,
    platform: str,
    kind: str,
    status: str = "posted",
    target_name: str = "",
    target_url: str = "",
    permalink: str = "",
    content: str = "",
    source_ref: str = "",
    ref: str = "",
    error: str = "",
    posted_at: str | None = None,
) -> str | None:
    """Record one published post/comment. Never raises — returns id or ``None``.

    ``ref`` is the natural dedup key (e.g. the third-party post id for a comment);
    falls back to source_ref / permalink / target_url. ``content`` is truncated to
    keep rows light.
    """
    try:
        return _repo().record(
            {
                "platform": platform,
                "kind": kind,
                "status": status,
                "target_name": target_name,
                "target_url": target_url,
                "permalink": permalink,
                "content": (content or "")[:500],
                "source_ref": source_ref,
                "ref": ref,
                "error": error[:300] if error else "",
                "posted_at": posted_at,
            }
        )
    except Exception as exc:  # logging must never break a publish
        logger.warning("engagements record_publish failed: %s", exc)
        return None


def list_engagements(
    *,
    platform: str | None = None,
    kind: str | None = None,
    status: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    return _repo().list_engagements(platform=platform, kind=kind, status=status, limit=limit)


def counts() -> dict[str, int]:
    return _repo().counts()


def posted_comment_post_ids(platform: str, post_ids: list[str]) -> set[str]:
    """Of ``post_ids``, which already have a POSTED comment recorded (dedup guard)."""
    return _repo().posted_comment_post_ids(platform, post_ids)
