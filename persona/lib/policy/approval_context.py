"""Approval context — runtime data the approval check needs.

Constructed once per runner (from logs/engagement_log.jsonl), passed
into every `requires_approval(item, ctx)` call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class ApprovalContext:
    """Inputs to the approval-policy check beyond the queue item itself.

    Attributes:
        previously_posted: Set of `target_name`s we've previously
            engaged with (filter applied — see lib.engagement.history.
            posted_targets, default {"comment", "like"}).
        template_usage: `{target → {snippet → most_recent_date}}` for the
            30-day template-reuse rule. See lib.engagement.history.
            template_usage().
        today: Current date (UTC) for window math. Defaulted to None so
            tests can inject a fixed date; runtime callers leave default.
    """

    previously_posted: frozenset[str] = field(default_factory=frozenset)
    template_usage: dict[str, dict[str, date]] = field(default_factory=dict)
    today: date | None = None
