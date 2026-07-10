"""
Monthly cleanup for DogFoodAndFun social automation.
Run on the 1st of each month to prune stale data and archive logs.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

BASE = Path(__file__).parent.parent
STATE_DIR = BASE / ".claude" / "state"
LOGS_DIR = BASE / "logs"
DATA_DIR = BASE / "data"
ARCHIVE_DIR = BASE / "logs" / "archive"

DEDUP_TTL_DAYS = 60
RATE_LIMIT_KEEP_DAYS = 7
QUEUE_KEEP_DAYS = 30
LOG_ARCHIVE_AFTER_DAYS = 30


def cleanup_dedup_cache() -> dict:
    """Remove expired dedup entries (> 60 days old)."""
    cache_file = STATE_DIR / "dedup_cache.json"
    if not cache_file.exists():
        return {"removed": 0, "remaining": 0}

    with cache_file.open() as f:
        cache = json.load(f)

    cutoff = (date.today() - timedelta(days=DEDUP_TTL_DAYS)).isoformat()
    removed = 0

    for platform in list(cache.keys()):
        for post_id in list(cache[platform].keys()):
            if cache[platform][post_id].get("engaged_at", "9999") < cutoff:
                del cache[platform][post_id]
                removed += 1
        if not cache[platform]:
            del cache[platform]

    with cache_file.open("w") as f:
        json.dump(cache, f, indent=2)

    remaining = sum(len(v) for v in cache.values())
    return {"removed": removed, "remaining": remaining}


def cleanup_rate_limit_tracker() -> dict:
    """Remove rate limit entries older than 7 days (keep recent history)."""
    tracker_file = STATE_DIR / "rate_limit_tracker.json"
    if not tracker_file.exists():
        return {"removed_days": 0}

    with tracker_file.open() as f:
        tracker = json.load(f)

    cutoff = (date.today() - timedelta(days=RATE_LIMIT_KEEP_DAYS)).isoformat()
    old_keys = [k for k in tracker if k < cutoff]

    for k in old_keys:
        del tracker[k]

    with tracker_file.open("w") as f:
        json.dump(tracker, f, indent=2)

    return {"removed_days": len(old_keys)}


def cleanup_comment_queue() -> dict:
    """Remove completed/skipped queue items older than 30 days."""
    queue_file = STATE_DIR / "comment_queue.json"
    if not queue_file.exists():
        return {"removed": 0, "remaining": 0}

    with queue_file.open() as f:
        queue = json.load(f)

    cutoff = (date.today() - timedelta(days=QUEUE_KEEP_DAYS)).isoformat()
    terminal_statuses = {
        "posted",
        "skipped_duplicate",
        "skipped_own_post",
        "USER_SKIPPED",
        "VALIDATION_FAILED",
        "POST_UNAVAILABLE",
    }

    before = len(queue)
    queue = [
        item
        for item in queue
        if item.get("status") == "pending"
        or item.get("queued_at", "9999")[:10] >= cutoff
        or item.get("status") not in terminal_statuses
    ]

    with queue_file.open("w") as f:
        json.dump(queue, f, indent=2)

    return {"removed": before - len(queue), "remaining": len(queue)}


def archive_engagement_log() -> dict:
    """Move log entries older than 30 days into a monthly archive file."""
    log_file = LOGS_DIR / "engagement_log.jsonl"
    if not log_file.exists():
        return {"archived": 0, "remaining": 0}

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = (date.today() - timedelta(days=LOG_ARCHIVE_AFTER_DAYS)).isoformat()

    current_entries = []
    archive_entries = []

    with log_file.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("date", "9999") < cutoff:
                    archive_entries.append(line)
                else:
                    current_entries.append(line)
            except Exception:
                current_entries.append(line)  # keep malformed lines in current

    if archive_entries:
        month_str = date.today().strftime("%Y-%m")
        archive_file = ARCHIVE_DIR / f"engagement_log_{month_str}.jsonl"
        with archive_file.open("a") as f:
            f.write("\n".join(archive_entries) + "\n")

    with log_file.open("w") as f:
        f.write("\n".join(current_entries) + ("\n" if current_entries else ""))

    return {"archived": len(archive_entries), "remaining": len(current_entries)}


def reset_site_cache() -> None:
    """Force-expire the site content cache so next run rebuilds it fresh."""
    cache_file = DATA_DIR / "site_content_cache.json"
    cache_file.write_text(
        json.dumps({"cached_at": None, "recent_posts": [], "content_summary": {}}, indent=2)
    )


def run_cleanup() -> None:
    print(f"\n{'=' * 50}")
    print(f"Monthly Cleanup — {date.today().isoformat()}")
    print(f"{'=' * 50}\n")

    result = cleanup_dedup_cache()
    print(
        f"✅ Dedup cache:       removed {result['removed']} expired entries, {result['remaining']} remaining"
    )

    result = cleanup_rate_limit_tracker()
    print(f"✅ Rate limit tracker: removed {result['removed_days']} old days")

    result = cleanup_comment_queue()
    print(
        f"✅ Comment queue:     removed {result['removed']} completed items, {result['remaining']} remaining"
    )

    result = archive_engagement_log()
    print(
        f"✅ Engagement log:    archived {result['archived']} old entries, {result['remaining']} active"
    )

    reset_site_cache()
    print("✅ Site cache:        force-expired (will rebuild on next run)")

    print("\nCleanup complete.\n")


if __name__ == "__main__":
    run_cleanup()
