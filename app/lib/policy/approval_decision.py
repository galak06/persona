"""Approval decision — output of `requires_approval`.

The reason field is a `Literal` (string enum) so callers can branch
on it without string-matching free-form text. Each reason maps to a
distinct line of justification in the Telegram approval message and
to a distinct row in the per-skill approval-rate metric.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ApprovalReason = Literal[
    "manual_flag",  # item.requires_approval=True from upstream
    "ig_platform",  # all IG comments require approval
    "wp_platform",  # all WP replies require approval (own-site, brand-facing)
    "url_in_draft",  # draft contains a brand-site URL (host from settings.site.url)
    "first_post_to_target",  # never engaged with this group/hashtag before
    "template_reused_recently",  # same template used in same target within 30d
    "auto_approved",  # no rule fired — safe to post without approval
]


@dataclass(frozen=True)
class ApprovalDecision:
    """Result of `requires_approval`.

    Attributes:
        needed: True iff the item must go through manual approval.
        reason: Which rule fired (or `auto_approved` when none did).
            Always set so callers don't have to check `needed` first
            to know what to log.
    """

    needed: bool
    reason: ApprovalReason
