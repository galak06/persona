"""
DogFoodAndFun — Agent Status Dashboard
Shows schedule, last run results, rate limit usage, and queue state.

Usage:
    python scripts/status.py
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SCHEDULE_FILE = PROJECT_ROOT / "schedule.json"
LAST_RUN_FILE = PROJECT_ROOT / ".claude/state/last_run.json"
RATE_FILE = PROJECT_ROOT / ".claude/state/rate_limit_tracker.json"
DEDUP_FILE = PROJECT_ROOT / ".claude/state/dedup_cache.json"
QUEUE_FILE = PROJECT_ROOT / ".claude/state/comment_queue.json"
PENDING_FILE = PROJECT_ROOT / ".claude/state/pending_groups.json"
LOG_FILE = PROJECT_ROOT / "logs/engagement_log.jsonl"
ERROR_LOG = PROJECT_ROOT / "logs/errors.log"


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return default


def fmt_ago(iso: str) -> str:
    """Return human-readable time since an ISO timestamp."""
    if not iso:
        return "never"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        delta = datetime.now(UTC) - dt
        secs = int(delta.total_seconds())
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        return f"{secs // 86400}d ago"
    except Exception:
        return iso[:16]


def status_icon(last_run_entry: dict) -> str:
    status = last_run_entry.get("status", "")
    if status == "success":
        return "✅"
    if status == "FAILED":
        return "❌"
    if last_run_entry:
        return "⚠️ "
    return "⬜"


def main() -> None:
    schedule = load_json(SCHEDULE_FILE, {})
    last_run = load_json(LAST_RUN_FILE, {})
    rate_data = load_json(RATE_FILE, {})
    dedup = load_json(DEDUP_FILE, {})
    queue = load_json(QUEUE_FILE, [])
    pending = load_json(PENDING_FILE, [])

    today = date.today().isoformat()
    today_rate = rate_data.get(today, {})

    print()
    print("=" * 62)
    print(f"  DogFoodAndFun — Agent Status  ({today})")
    print("=" * 62)

    # ── Schedule & last run ──────────────────────────────────────────
    print()
    print("  SCHEDULE & LAST RUN")
    print(f"  {'Agent':<22} {'Schedule':<26} {'Last run':<12} {'Result'}")
    print(f"  {'-' * 22} {'-' * 26} {'-' * 12} {'-' * 20}")

    skill_keys = {
        "site-analyzer": "site_analyzer",
        "fb-scanner": "fb_scanner",
        "ig-scanner": "ig_scanner",
        "comment-composer": "comment_composer",
        "fb-group-scout": "fb_group_scout",
    }

    for task in schedule.get("tasks", []):
        skill = task["skill"]
        key = skill_keys.get(skill, skill.replace("-", "_"))
        entry = last_run.get(key, {})
        icon = status_icon(entry)
        ago = fmt_ago(entry.get("last_run_at", ""))
        sched = task["schedule"]["human"].replace("Daily at ", "")
        sched = (
            f"Daily {sched}"
            if "Daily" not in task["schedule"]["human"]
            else task["schedule"]["human"]
        )

        # Extra stat
        stat = ""
        if key == "fb_scanner":
            stat = (
                f"groups={entry.get('groups_scanned', '?')} queued={entry.get('posts_queued', '?')}"
            )
        elif key == "ig_scanner":
            stat = f"liked={entry.get('posts_liked', '?')} queued={entry.get('posts_queued_for_comment', '?')}"
        elif key == "comment_composer":
            stat = f"posted={entry.get('comments_posted', '?')} skipped={entry.get('comments_skipped', '?')}"
        elif key == "fb_group_scout":
            stat = f"sent={entry.get('join_requests_sent', '?')}"

        print(f"  {icon} {skill:<20} {sched:<26} {ago:<12} {stat}")

    # ── Rate limits today ────────────────────────────────────────────
    print()
    print("  RATE LIMITS TODAY")
    limits = {
        "facebook:comment": 5,
        "facebook:group_visit": 6,
        "instagram:like": 8,
        "instagram:ig_comment": 2,
    }
    for key, limit in limits.items():
        used = today_rate.get(key, 0)
        remaining = limit - used
        bar = "█" * used + "░" * remaining
        print(f"  {key:<30} {used}/{limit}  [{bar}]")

    # ── Comment queue ────────────────────────────────────────────────
    print()
    print("  COMMENT QUEUE")
    pending_items = [q for q in queue if q.get("status") == "pending"]
    posted_today = [q for q in queue if q.get("posted_at", "")[:10] == today]
    skipped = [
        q
        for q in queue
        if q.get("status") in ("USER_SKIPPED", "VALIDATION_FAILED", "skipped_duplicate")
    ]
    fb_pending = [q for q in pending_items if q.get("platform") == "facebook"]
    ig_pending = [q for q in pending_items if q.get("platform") == "instagram"]

    print(f"  Pending:        {len(pending_items)} ({len(fb_pending)} FB, {len(ig_pending)} IG)")
    print(f"  Posted today:   {len(posted_today)}")
    print(f"  Skipped:        {len(skipped)}")

    if pending_items:
        print()
        print(f"  {'#':<3} {'Platform':<12} {'Score':<7} {'Preview'}")
        for i, item in enumerate(pending_items[:5], 1):
            preview = item.get("post_text", "")[:45].replace("\n", " ")
            score = item.get("relevance_score", 0)
            plat = item.get("platform", "?")
            item.get("group_name") or item.get("hashtag", "")
            print(f"  {i:<3} {plat:<12} {score:<7.2f} {preview}...")
        if len(pending_items) > 5:
            print(f"  ... and {len(pending_items) - 5} more")

    # ── Dedup cache ──────────────────────────────────────────────────
    print()
    print("  DEDUP CACHE (60-day rolling)")
    for platform, posts in dedup.items():
        print(f"  {platform:<15} {len(posts)} posts tracked")
    if not dedup:
        print("  Empty")

    # ── FB group scout ───────────────────────────────────────────────
    print()
    print("  FB GROUP SCOUT")
    print(f"  Pending groups queue: {len(pending)} group(s) saved, not yet joined")
    if pending:
        for g in pending[:3]:
            mc = f"{g.get('member_count', 0):,}" if g.get("member_count") else "?"
            priv = g.get("privacy", "?").upper()
            print(
                f"    • [{priv:7}] score={g.get('score', '?'):>3}  members={mc:>8}  {g.get('name', '?')[:40]}"
            )
        if len(pending) > 3:
            print(f"    ... and {len(pending) - 3} more")

    # ── Recent errors ────────────────────────────────────────────────
    print()
    print("  RECENT ERRORS (last 5)")
    if ERROR_LOG.exists():
        lines = ERROR_LOG.read_text().strip().splitlines()
        recent = [
            ln
            for ln in lines
            if today in ln or (date.today() - timedelta(days=1)).isoformat() in ln
        ]
        if recent:
            for line in recent[-5:]:
                print(f"  ⚠️  {line[:100]}")
        else:
            print("  No errors today ✅")
    else:
        print("  No error log yet ✅")

    print()
    print("=" * 62)
    print()


if __name__ == "__main__":
    main()
