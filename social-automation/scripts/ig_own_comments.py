"""IG Own-Post Comment Handler — replies to visitor comments on our IG media
via Graph API after Telegram approval. Hourly cron via launchd.

Persists seen comment ids in .claude/state/own_post_comments_seen.json (30d
rolling). A flock on .claude/state/ig_own_comments.lock prevents concurrent
runs from racing on the same un-seen comment during the Telegram wait.

Usage:
    python scripts/ig_own_comments.py             # real run
    python scripts/ig_own_comments.py --dry-run   # scan + draft only
    python scripts/ig_own_comments.py --limit 3   # process at most N new
"""

from __future__ import annotations

import argparse
import atexit
import fcntl
import json
import os
import random
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from lib.bootstrap import init_script
settings, log = init_script(__name__)
sys.path.insert(0, str(PROJECT_ROOT / "recipe-publisher"))

from local_env import load_local_env
from lib.logger import log_progress

# Bridge .claude/settings.local.json secrets into os.environ. Required for
# launchd cron — no Claude harness here to inject them. Safe no-op when env
# vars are already set (manual runs win).
load_local_env()

from publishers.instagram import (
    InstagramError,
    list_media_comments,
    list_recent_user_media,
    reply_to_instagram_comment,
)

from notifier import (
    request_approval,
    skill_error,
    skill_finished,
    skill_skipped,
    skill_started,
)
from rate_limiter import DELAY_RANGES, can_act, record_action
from reply_drafter import draft_reply

SEEN_FILE = PROJECT_ROOT / ".claude" / "state" / "own_post_comments_seen.json"
LOCK_FILE = PROJECT_ROOT / ".claude" / "state" / "ig_own_comments.lock"
ENGAGEMENT_LOG = PROJECT_ROOT / "logs" / "engagement_log.jsonl"
ERROR_LOG = PROJECT_ROOT / "logs" / "errors.log"

MAX_MEDIA_LOOKBACK = 10
MEDIA_AGE_DAYS = 14
SEEN_RETENTION_DAYS = 30
DEFAULT_SELF_HANDLE = "dogfoodandfun"


def _load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _log_error(msg: str) -> None:
    ts = datetime.now(UTC).isoformat()
    ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
    with ERROR_LOG.open("a") as f:
        f.write(f"[{ts}] {msg}\n")


def _log_engagement(action: str, target: str, content: str) -> None:
    entry = {
        "date": date.today().isoformat(),
        "timestamp": datetime.now(UTC).isoformat(),
        "action": action,
        "platform": "instagram",
        "target_name": target,
        "content": content[:200],
    }
    ENGAGEMENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with ENGAGEMENT_LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def _recent_media() -> list[dict]:
    """Pull recent IG media directly from Meta — single source of truth.

    Earlier versions read from recipe-publisher/state/published_recipes.json,
    but that file occasionally stored container IDs that aren't queryable as
    real media. The Graph API's /{ig_user_id}/media endpoint is authoritative.
    Returns dicts shaped like {ig_media_id, title, published_at} so the rest
    of the scanner doesn't care about the source change.
    """
    cutoff = datetime.now(UTC) - timedelta(days=MEDIA_AGE_DAYS)
    raw = list_recent_user_media(limit=MAX_MEDIA_LOOKBACK)
    out: list[dict] = []
    for m in raw:
        ts_str = m.get("timestamp") or ""
        try:
            ts = datetime.fromisoformat(ts_str.replace("+0000", "+00:00"))
        except ValueError:
            continue
        if ts < cutoff:
            continue
        caption = (m.get("caption") or "").strip().replace("\n", " ")
        out.append(
            {
                "ig_media_id": m["id"],
                "title": caption[:60] or m["id"],
                "published_at": ts_str,
            }
        )
    return out


def _acquire_singleton_lock():
    """Try to grab an exclusive flock on LOCK_FILE. Returns the file handle on
    success, None if another instance is already holding it. Lock is released
    automatically by the kernel on process exit (crash/SIGKILL included);
    atexit + finally cover normal exits.
    """
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    fp = LOCK_FILE.open("w")
    try:
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fp.close()
        return None
    fp.write(f"pid={os.getpid()} started={datetime.now(UTC).isoformat()}\n")
    fp.flush()

    def _release():
        try:
            fcntl.flock(fp.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            fp.close()
        except Exception:
            pass

    atexit.register(_release)
    return fp


def _prune_seen(seen: dict) -> dict:
    """Drop entries older than SEEN_RETENTION_DAYS so the file doesn't grow unbounded."""
    cutoff = datetime.now(UTC) - timedelta(days=SEEN_RETENTION_DAYS)
    pruned = {}
    for cid, meta in seen.items():
        ts = meta.get("seen_at", "")
        try:
            if datetime.fromisoformat(ts) >= cutoff:
                pruned[cid] = meta
        except ValueError:
            continue
    return pruned


def _handle_comment(
    comment: dict,
    media_entry: dict,
    *,
    dry_run: bool,
) -> tuple[str, str]:
    """Draft + approve + post a reply to one visitor comment.

    Returns (action, detail): action ∈ {replied, skipped, pending, failed, rate_limited}
    """
    cid = comment["id"]
    text = (comment.get("text") or "").strip()
    username = comment.get("username") or "there"
    title = media_entry.get("title") or "our recipe"

    if not text:
        return "skipped", "empty text"

    # Ground the draft in the recipe context. We treat the recipe title/caption
    # as "our comment" so reply_drafter frames the response as a follow-up in
    # the thread; the site cache adds recipe-aware detail.
    draft = draft_reply(
        our_comment=f"Just posted: {title}",
        their_reply=text,
        their_author=username,
    )

    if dry_run:
        print(f"    [dry-run] would reply to @{username}: {draft[:100]}…", flush=True)
        return "skipped", "dry-run"

    if not can_act("instagram", "own_reply"):
        return "rate_limited", "daily own_reply cap hit"

    result = request_approval(
        platform="instagram",
        group_or_hashtag=f"own post: {title}",
        post_preview=text,
        draft_comment=draft,
        relevance_score=1.0,
        timeout_hours=12,
    )
    action = result["action"]
    if action == "pending":
        return "pending", "telegram unreachable"
    if action not in ("approved", "edited"):
        return "skipped", f"user action: {action}"

    final = result["comment"]
    try:
        reply_id = reply_to_instagram_comment(cid, final)
    except InstagramError as exc:
        _log_error(f"IG_OWN_REPLY_FAILED cid={cid}: {exc}")
        return "failed", str(exc)

    record_action("instagram", "own_reply")
    _log_engagement("own_reply", f"ig:{title}", final)
    print(f"    ✅ reply posted id={reply_id}", flush=True)
    return "replied", reply_id


def run(*, dry_run: bool, limit: int) -> int:
    print("=== IG Own-Post Comments ===\n", flush=True)

    lock_fp = _acquire_singleton_lock()
    if lock_fp is None:
        print("Another ig_own_comments instance is already running. Exiting.", flush=True)
        skill_skipped("ig-own-comments", "another instance running — flock held")
        return 0
    try:
        return _run_locked(dry_run=dry_run, limit=limit)
    finally:
        try:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_UN)
            lock_fp.close()
        except Exception:
            pass


def _run_locked(*, dry_run: bool, limit: int) -> int:
    media_list = _recent_media()
    if not media_list:
        print("No recent IG media found — nothing to scan.", flush=True)
        skill_skipped("ig-own-comments", "no recent media")
        return 0

    self_handle = (os.environ.get("IG_HANDLE") or DEFAULT_SELF_HANDLE).lstrip("@").lower()
    seen = _prune_seen(_load_json(SEEN_FILE, default={}))
    processed = 0
    counts = {"replied": 0, "skipped": 0, "pending": 0, "failed": 0, "rate_limited": 0}

    skill_started(
        "ig-own-comments",
        f"scanning {len(media_list)} recent IG posts",
    )

    for idx, media in enumerate(media_list):
        mid = media["ig_media_id"]
        title = media.get("title") or mid
        log_progress(idx + 1, len(media_list), f"IG: {title}")

        try:
            comments = list_media_comments(mid)
        except InstagramError as exc:
            _log_error(f"IG_LIST_COMMENTS_FAILED media={mid}: {exc}")
            print(f"    ! list_media_comments failed: {exc}", flush=True)
            continue

        new_comments = [
            c
            for c in comments
            if c.get("id")
            and c["id"] not in seen
            and (c.get("username") or "").lower() != self_handle
            and not c.get("hidden")
        ]
        print(f"    {len(comments)} total / {len(new_comments)} new", flush=True)

        for cix, comment in enumerate(new_comments):
            if processed >= limit:
                print("    limit hit — stopping", flush=True)
                break

            # Defense-in-depth: even with the singleton lock, claim the comment
            # in seen.json BEFORE the long Telegram round-trip so any rogue
            # second invocation that bypasses flock (e.g. NFS) won't double-post.
            if not dry_run:
                seen[comment["id"]] = {
                    "seen_at": datetime.now(UTC).isoformat(),
                    "media_id": mid,
                    "action": "in_flight",
                }
                _save_json(SEEN_FILE, seen)

            action, detail = _handle_comment(comment, media, dry_run=dry_run)
            counts[action] = counts.get(action, 0) + 1

            # Persist: the in_flight claim becomes permanent on definitive
            # outcomes; rolled back on `pending` (Telegram unreachable) so the
            # next run reconsiders, and on dry-run skipped to avoid poisoning.
            persist = action != "pending" and not (dry_run and action == "skipped")
            if persist:
                seen[comment["id"]] = {
                    "seen_at": datetime.now(UTC).isoformat(),
                    "media_id": mid,
                    "action": action,
                }
                _save_json(SEEN_FILE, seen)
            elif not dry_run:
                seen.pop(comment["id"], None)
                _save_json(SEEN_FILE, seen)

            processed += 1

            # Human-paced spacing between replies so the thread doesn't look botty.
            if action == "replied" and cix < len(new_comments) - 1 and processed < limit:
                lo, hi = DELAY_RANGES["instagram:own_reply"]
                delay = random.uniform(lo, hi)
                print(f"    waiting {delay:.0f}s", flush=True)
                time.sleep(delay)

        if processed >= limit:
            break

    summary = (
        f"📝 replied={counts['replied']} skipped={counts['skipped']} "
        f"pending={counts['pending']} failed={counts['failed']} "
        f"ratelimited={counts['rate_limited']}"
    )
    print(f"\n=== Done === {summary}", flush=True)
    skill_finished("ig-own-comments", summary, success=counts["failed"] == 0)
    return 0 if counts["failed"] == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ig-own-comments")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="scan + draft only; no Telegram, no API reply",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="max new comments to process this run (default 10)",
    )
    args = parser.parse_args(argv)
    try:
        return run(dry_run=args.dry_run, limit=args.limit)
    except Exception as exc:
        skill_error("ig-own-comments", str(exc))
        raise


if __name__ == "__main__":
    sys.exit(main())
