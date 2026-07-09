"""Data contracts for the FB groups DB layer.

The repository works on the plain group **dicts** the rest of the codebase
already uses (the groups_tracker shape), so there is no heavy row dataclass —
``GROUP_COLUMNS`` defines which keys map to typed columns; everything else
round-trips through the ``extra`` JSON column.
"""

from __future__ import annotations

import re


class GroupStatus:
    """Allowed values for ``fb_groups.status``."""

    JOINED: str = "joined"
    JOIN_REQUESTED: str = "join_requested"
    REJECTED: str = "rejected"
    NOT_JOINED_YET: str = "not_joined_yet"

    ALL: frozenset[str] = frozenset(
        {"joined", "join_requested", "rejected", "not_joined_yet"}
    )


class PostingMode:
    """Allowed values for ``fb_groups.posting_mode`` (posting eligibility)."""

    DIRECT: str = "direct"
    ADMINS_ONLY: str = "admins_only"
    ADMIN_APPROVAL: str = "admin_approval"
    LINKS_BLOCKED: str = "links_blocked"
    BLOCKED: str = "blocked"
    UNKNOWN: str = "unknown"

    ALL: frozenset[str] = frozenset(
        {"direct", "admins_only", "admin_approval", "links_blocked", "blocked", "unknown"}
    )


def slugify(text: str) -> str:
    """Lowercase, hyphenate, strip non-alphanumerics."""
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")


def group_id_from_url(group_url: str) -> str:
    """Stable PK from a group URL — the FB numeric id when present, else a slug.

    ``https://www.facebook.com/groups/219924639809303/`` -> ``219924639809303``.
    """
    match = re.search(r"/groups/([^/?#]+)", group_url or "")
    if match:
        return slugify(match.group(1))
    return slugify(group_url) or "unknown"


# Group dict keys that map to dedicated typed columns. ``notes`` (JSON list) and
# any unmodeled keys (-> ``extra``) are handled separately by the repository.
GROUP_COLUMNS: tuple[str, ...] = (
    "group_url",
    "group_name",
    "status",
    "joined_at",
    "rules",
    "source_notification",
    "privacy",
    "member_count",
    "posting_mode",
    "self_promo_allowed",
    "category",
    "last_post_status",
    "last_post_caption",
    "last_post_permalink",
    "last_post_at",
    "last_reel_caption",
    "last_reel_post_at",
    "last_reel_post_permalink",
    "last_checked_at",
)

# Always emitted by _row_to_dict even when empty (API requires these).
_ALWAYS_KEYS: frozenset[str] = frozenset({"group_url", "group_name", "status"})
