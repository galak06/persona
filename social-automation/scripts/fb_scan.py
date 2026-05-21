"""
Facebook Group Scanner — CLI orchestrator.

Loops over joined groups, scores each post for {{brand.domain}} relevance, and
queues qualifying posts for comment-composer. All Playwright / DOM / login /
page-profile work lives in lib.engagement.adapters.facebook.FacebookGroupAdapter;
this script owns orchestration only: dedup, scoring, drafting, queue write,
rate-limit gating, 48h warmup gate, and last-run telemetry.

Usage:
    1. First time: python scripts/fb_login.py   (log in, save session)
    2. Then:       python scripts/fb_scan.py     (scan groups)
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

# Ensure lib is importable
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from lib.activity_log import log_trace
from lib.bootstrap import init_script

settings, log = init_script(__name__)

from comment_generator import score_relevance
from deduplication import is_duplicate, mark_engaged
from draft_helper import draft_comment_for_post
from group_warmup import COMMENT_WARMUP_HOURS, hours_until_warm, is_group_warm
from lib.engagement.adapter import OutboundAdapter
from lib.engagement.adapters.facebook import FacebookGroupAdapter
from lib.engagement.policy import EngagementPolicy
from lib.logger import log_progress, log_step
from notifier import skill_finished, skill_skipped, skill_started
from rate_limiter import can_act, print_status, record_action, wait_random_delay

QUEUE_FILE = settings.paths.comment_queue
LAST_RUN_FILE = settings.paths.last_run
SESSION_FILE = settings.paths.facebook_session
ERROR_LOG = settings.paths.logs_dir / "errors.log"
CONFIG_FILE = settings.paths.brand_dir / "config.json"


def load_config() -> dict:
    with CONFIG_FILE.open() as f:
        return json.load(f)


def log_error(msg: str) -> None:
    ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).isoformat()
    with ERROR_LOG.open("a") as f:
        f.write(f"[{ts}] {msg}\n")


def load_queue() -> list[dict]:
    if QUEUE_FILE.exists():
        with QUEUE_FILE.open() as f:
            return json.load(f)
    return []


def save_queue(queue: list[dict]) -> None:
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with QUEUE_FILE.open("w") as f:
        json.dump(queue, f, indent=2)


def load_last_run() -> dict:
    if LAST_RUN_FILE.exists():
        with LAST_RUN_FILE.open() as f:
            return json.load(f)
    return {}


def save_last_run(data: dict) -> None:
    LAST_RUN_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LAST_RUN_FILE.open("w") as f:
        json.dump(data, f, indent=2)


def _already_ran_today(last_run: dict) -> tuple[bool, str]:
    """Return (skip, summary_message). Skip is True only when the prior run succeeded today."""
    fb_last = last_run.get("fb_scanner", {})
    fb_last_date = (fb_last.get("last_run_at") or "")[:10]
    if fb_last_date == date.today().isoformat() and fb_last.get("status") == "success":
        msg = (
            f"Already ran today — scanned {fb_last.get('groups_scanned', 0)} groups, "
            f"queued {fb_last.get('posts_queued', 0)} posts"
        )
        return True, msg
    return False, ""


def run_fb_scan(adapter: OutboundAdapter | None = None) -> int:
    """Run the FB group scan and return number of posts queued.

    The adapter parameter exists for tests (FakeAdapter); in production the
    default FacebookGroupAdapter is constructed from the loaded config.
    """
    print("=== Facebook Group Scanner (CLI) ===\n", flush=True)
    log_trace("facebook", "Started Facebook group scan")

    # Sanity: session file must exist when using the real adapter.
    if adapter is None and not SESSION_FILE.exists():
        print("ERROR: No saved Facebook session found.")
        print("Run this first:  python scripts/fb_login.py")
        log_trace("facebook", "Aborted: No saved session")
        return 0

    # Already ran successfully today?
    last_run = load_last_run()
    skip, skip_msg = _already_ran_today(last_run)
    if skip:
        print("SKIP: fb_scanner already ran successfully today.")
        print("      Use --force to run again anyway.")
        skill_skipped("fb-scanner", skip_msg)
        if "--force" not in sys.argv:
            log_trace("facebook", "Skipped: already ran today")
            return 0

    # Pre-flight: rate limits
    if not can_act("facebook", "group_visit"):
        print("ABORT: Daily group visit limit reached. Try again tomorrow.")
        log_trace("facebook", "Aborted: Daily group visit limit reached")
        skill_skipped("fb-scanner", "Daily group visit limit reached")
        print_status()
        return 0

    skill_started("fb-scanner", "Scanning Facebook dog groups for posts to engage with")
    print_status()

    config = load_config()
    policy = EngagementPolicy.from_config(config)
    adapter = adapter or FacebookGroupAdapter(config)

    queue = load_queue()

    # Stats
    groups_scanned = 0
    posts_evaluated = 0
    posts_queued = 0
    posts_skipped_dedup = 0
    posts_skipped_score = 0
    posts_skipped_prefilter = 0
    high_confidence = 0
    needs_approval = 0

    try:
        sources = adapter.list_sources()
    except FileNotFoundError as e:
        print(f"ABORT: {e}")
        log_error(str(e))
        skill_skipped("fb-scanner", str(e))
        return 0

    print(f"Groups to scan: {len(sources)}")
    for s in sources:
        print(f"  - {s.name}")
    print()

    try:
        with adapter.session():
            log_step("Facebook session OK")

            for group_idx, source in enumerate(sources, 1):
                # Rate limit before each group visit
                if not can_act("facebook", "group_visit"):
                    print(
                        f"\nRate limit hit — stopping after {groups_scanned} groups.",
                        flush=True,
                    )
                    break

                # 48h warmup gate — newly joined groups need to age before we comment
                if not is_group_warm(source.url, COMMENT_WARMUP_HOURS):
                    remaining = hours_until_warm(source.url, COMMENT_WARMUP_HOURS)
                    print(
                        f"  Skipping {source.name} — in {COMMENT_WARMUP_HOURS}h warmup "
                        f"({remaining:.1f}h remaining)",
                        flush=True,
                    )
                    continue

                log_progress(group_idx, len(sources), f"Scanning: {source.name}")
                print(f"    URL: {source.url}", flush=True)

                # Record the visit BEFORE extraction so it's always counted
                try:
                    record_action("facebook", "group_visit")
                except RuntimeError as re:
                    print(f"    Rate limit hit: {re}")
                    break
                except Exception as re:
                    log_error(f"record_action failed for {source.name}: {re}")
                groups_scanned += 1

                try:
                    posts = list(adapter.iterate_posts(source))
                except Exception as e:
                    err_str = str(e).lower()
                    if "target page" in err_str or "context" in err_str or "closed" in err_str:
                        # Browser crash inside the adapter's session is unrecoverable
                        # here: the session contextmanager owns the browser, so we
                        # abort the rest of the scan gracefully rather than trying
                        # to rebuild a context the adapter encapsulates.
                        log_error(f"CONTEXT_CRASH: {source.name} — {e}")
                        print(f"    Browser context crashed — aborting scan: {e}")
                        break
                    log_error(f"Error scanning {source.name}: {e}")
                    print(f"    ERROR: {e}")
                    continue

                print(f"    Posts extracted: {len(posts)}")
                if not posts:
                    print("    Skipping group (no content extracted).")
                    # Delay before next group even when empty
                    if can_act("facebook", "group_visit"):
                        wait_random_delay("facebook", "group_visit")
                    continue

                for post in posts:
                    posts_evaluated += 1

                    snippet = post.text[:80].replace("\n", " ")
                    has_url = bool(post.post_url) and post.post_url != source.url
                    comment_count = int(post.platform_extra.get("comment_count", 0) or 0)
                    print(f"    [{posts_evaluated}] {snippet}...")
                    print(f"        url={'yes' if has_url else 'NO'} comments={comment_count}")

                    # Dedup check
                    if is_duplicate("facebook", post.post_id):
                        posts_skipped_dedup += 1
                        print("        SKIP: already engaged")
                        continue

                    # Platform pre-filter (FB: always None today)
                    reason = adapter.pre_filter(post)
                    if reason:
                        posts_skipped_prefilter += 1
                        print(f"        SKIP: pre_filter={reason}")
                        continue

                    # Score
                    category = str(post.platform_extra.get("category", "food") or "food")
                    meta = {"comment_count": comment_count, "hours_old": 12}
                    base_score = score_relevance(post.text, meta, group_category=category)
                    score = adapter.adjust_score(post, base_score)
                    print(f"        score={score} (threshold={policy.candidate_threshold})")

                    if not policy.is_candidate(score):
                        posts_skipped_score += 1
                        continue

                    # Queue it
                    requires_approval = policy.requires_approval(score)
                    draft_comment = draft_comment_for_post(
                        platform="facebook",
                        post_text=post.text,
                        group_or_hashtag=source.name,
                        post_url=post.post_url,
                    )
                    if not draft_comment:
                        log.info(
                            {
                                "event": "draft_inline_empty",
                                "platform": "facebook",
                                "post_url": post.post_url,
                            }
                        )
                    queue.append(
                        post.to_queue_record(
                            score=score,
                            draft=draft_comment,
                            requires_approval=requires_approval,
                            queued_at=datetime.now(UTC).isoformat(),
                        )
                    )
                    mark_engaged(
                        "facebook", post.post_id, "comment_queued", source.name
                    )
                    posts_queued += 1
                    if requires_approval:
                        needs_approval += 1
                    else:
                        high_confidence += 1

                    label = "APPROVAL" if requires_approval else "AUTO"
                    print(f"    QUEUED [{label}] score={score} id={post.post_id[:20]}")

                # Delay between group visits (skip after last group)
                if can_act("facebook", "group_visit"):
                    wait_random_delay("facebook", "group_visit")

    except RuntimeError as e:
        # Adapter raised on session entry (e.g., SESSION_EXPIRED, no session file)
        log_error(str(e))
        print(f"ABORT: {e}")
        if "SESSION_EXPIRED" in str(e):
            print("Re-run:  python scripts/fb_login.py")
        skill_skipped("fb-scanner", str(e))
        return 0

    # Save queue
    save_queue(queue)

    # Update last run — mark success so re-run guard works
    last_run["fb_scanner"] = {
        "last_run_at": datetime.now(UTC).isoformat(),
        "groups_scanned": groups_scanned,
        "posts_queued": posts_queued,
        "status": "success",
    }
    save_last_run(last_run)

    # Summary
    summary = (
        f"📘 Groups scanned: {groups_scanned}/{len(sources)}\n"
        f"📝 Posts queued: {posts_queued} "
        f"(✅ {high_confidence} auto, 👀 {needs_approval} need approval)\n"
        f"⏭️ Skipped: {posts_skipped_dedup} dedup, {posts_skipped_score} low score"
    )
    log_trace("facebook", f"Scan complete: {groups_scanned} groups, {posts_queued} queued")
    print(f"""
=== Facebook Scan Complete ===
Groups scanned: {groups_scanned} / {len(sources)}
Posts evaluated: {posts_evaluated}
Posts queued for comments: {posts_queued}
  - High confidence (score >= {policy.approval_threshold}): {high_confidence}
  - Needs approval ({policy.candidate_threshold}-{policy.approval_threshold}): {needs_approval}
Posts skipped — already engaged: {posts_skipped_dedup}
Posts skipped — below threshold: {posts_skipped_score}
Posts skipped — pre-filter: {posts_skipped_prefilter}
""")
    print_status()
    skill_finished("fb-scanner", summary)
    return posts_queued


if __name__ == "__main__":
    run_fb_scan()
