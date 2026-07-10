"""Engagement-log helpers — append actions, reconstruct history.

Replaces 4 inline `log_engagement` reimplementations and 3 different
engagement-history reconstruction patterns. Single source of truth
for `logs/engagement_log.jsonl`.

The canonical "history" filter is `action ∈ {"comment", "like"}` —
a group is "previously engaged" only when there's been conversational
contact. Group-posts (publishing) do NOT count as engagement; they're
a different concern (broadcast, not conversation). This matches the
intent of the comment-composer SKILL.md and is more conservative
(more approval prompts for first conversational comments) than the
prior `comment_composer_graph` behavior, which counted any logged
action.
"""

from lib.engagement.history import (
    DEFAULT_ENGAGEMENT_ACTIONS,
    posted_targets,
    template_usage,
)
from lib.engagement.log import log_engagement

__all__ = [
    "DEFAULT_ENGAGEMENT_ACTIONS",
    "log_engagement",
    "posted_targets",
    "template_usage",
]
