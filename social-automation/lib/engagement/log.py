"""Append a single engagement action to the JSONL log.

Replaces 4 reimplementations across scripts/. Same on-disk format,
same field names — drop-in replacement.
"""

from __future__ import annotations

import json
from lib.config import settings
from datetime import UTC, date, datetime
from pathlib import Path

_DEFAULT_LOG_FILE = settings.paths.logs_dir / "engagement_log.jsonl"
_CONTENT_TRUNCATE_CHARS = 200


def log_engagement(
    action: str,
    platform: str,
    target: str,
    content: str,
    *,
    log_file: Path | None = None,
) -> None:
    """Append one engagement record to the JSONL log.

    Args:
        action: `comment` | `like` | `group_post` | `reply` | `own_reply` | etc.
            Free-form string — `posted_targets()` filters by this.
        platform: `facebook` | `instagram` | `wordpress`.
        target: Group name, hashtag, post URL, or other identifier the
            engagement-history reconstruction looks up later.
        content: The text of the action. Truncated to 200 chars on disk.
        log_file: Override path (tests). Default
            `logs/engagement_log.jsonl` under the project root.

    Side effects:
        Appends one JSON-encoded line to `log_file`. Creates parent dir
        if needed. Not atomic — append-only writes are crash-safe at
        the line level (a half-written line is malformed JSON and will
        be skipped by the history parser).
    """
    path = log_file or _DEFAULT_LOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "date": date.today().isoformat(),
        "timestamp": datetime.now(UTC).isoformat(),
        "action": action,
        "platform": platform,
        "target_name": target,
        "content": content[:_CONTENT_TRUNCATE_CHARS],
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
