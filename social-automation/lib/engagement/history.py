"""Reconstruct engagement history from the JSONL log.

Two views:
    - `posted_targets(actions=...)` — set of target_names we've engaged
      with via the listed action types
    - `template_usage(window_days=30)` — map of `{target → {snippet → date}}`
      for the 30-day template-reuse rule referenced in
      `.claude/skills/comment-composer/SKILL.md:42-62`

Both replace inline reconstruction patterns in:
    - scripts/comment_approver.py:51-62 (build_engagement_history, comment+like filter)
    - scripts/comment_composer_graph.py:60-70 (_engagement_history, no filter — drift)
    - .claude/skills/comment-composer/SKILL.md:42-62 (markdown — comment+like filter + template_usage)

Choosing the comment+like filter as canonical (most conservative,
matches SKILL.md intent). The graph version's no-filter behavior was
an unintended drift that would suppress approval prompts for first
conversational comments to publishing-only groups.
"""

from __future__ import annotations

import json
from lib.config import settings
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

DEFAULT_ENGAGEMENT_ACTIONS: frozenset[str] = frozenset({"comment", "like"})
"""Canonical filter — only conversational actions count toward
"previously engaged". Excludes group_post (broadcast) and own_reply
(responding to our own posts)."""

_DEFAULT_LOG_FILE = settings.paths.logs_dir / "engagement_log.jsonl"
_TEMPLATE_SNIPPET_CHARS = 40


def posted_targets(
    *,
    actions: frozenset[str] | None = None,
    log_file: Path | None = None,
) -> set[str]:
    """Return the set of `target_name`s we've engaged with via the given actions.

    Args:
        actions: Set of action types that count as "engaged". Default
            is `DEFAULT_ENGAGEMENT_ACTIONS` (comment + like). Pass
            an empty frozenset to count any action.
        log_file: Override path (tests).

    Returns:
        Set of target names (group names, hashtags, etc.). Lines that
        fail to parse are silently skipped — the log can grow large
        and one malformed line shouldn't break history.
    """
    filter_actions = DEFAULT_ENGAGEMENT_ACTIONS if actions is None else actions
    path = log_file or _DEFAULT_LOG_FILE
    if not path.exists():
        return set()

    targets: set[str] = set()
    for entry in _iter_records(path):
        action = entry.get("action")
        if filter_actions and action not in filter_actions:
            continue
        target = entry.get("target_name")
        if isinstance(target, str) and target:
            targets.add(target)
    return targets


def template_usage(
    *,
    window_days: int = 30,
    snippet_chars: int = _TEMPLATE_SNIPPET_CHARS,
    log_file: Path | None = None,
    today: date | None = None,
) -> dict[str, dict[str, date]]:
    """Return `{target → {snippet → most_recent_date}}` for templates used recently.

    Powers the "don't reuse the same template in the same group within
    30 days" rule.

    Args:
        window_days: Only consider records from the last N days.
        snippet_chars: Truncate `content` to this many chars to form
            the template key. Default 40 matches SKILL.md.
        log_file: Override path (tests).
        today: Override "current" date (tests).

    Returns:
        Nested dict — outer key is target_name, inner key is content
        snippet, value is the most-recent date that snippet was posted
        in that target.
    """
    path = log_file or _DEFAULT_LOG_FILE
    if not path.exists():
        return {}

    cutoff = (today or datetime.now(UTC).date()) - timedelta(days=window_days)
    usage: dict[str, dict[str, date]] = {}

    for entry in _iter_records(path):
        if entry.get("action") != "comment":
            continue
        target = entry.get("target_name")
        content = entry.get("content")
        date_value = entry.get("date", "")
        when = _parse_date(date_value if isinstance(date_value, str) else "")
        if not (isinstance(target, str) and isinstance(content, str) and when):
            continue
        if when < cutoff:
            continue
        snippet = content[:snippet_chars]
        target_map = usage.setdefault(target, {})
        prior = target_map.get(snippet)
        if prior is None or when > prior:
            target_map[snippet] = when
    return usage


def _iter_records(path: Path) -> list[dict[str, object]]:
    """Read the JSONL log into a list, skipping malformed lines.

    Returned as a list (not a generator) so callers can iterate twice
    without re-reading; the log is small enough (~daily appends) that
    materializing is fine.
    """
    records: list[dict[str, object]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                records.append(obj)
    return records


def _parse_date(value: str) -> date | None:
    """Parse 'YYYY-MM-DD' or ISO timestamp prefix into a `date`. None on failure."""
    if not value:
        return None
    try:
        # `date` field is YYYY-MM-DD; `timestamp` field is full ISO.
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
