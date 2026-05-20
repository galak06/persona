"""
Instagram Hashtag Scanner — orchestration layer.

Loops over hashtags via InstagramHashtagAdapter, runs base relevance scoring +
dedup + rate-limit + draft + queue write. All platform-specific work
(Playwright session, hashtag iteration, DOM scraping, like click, competitor /
own-account / age guards, IG-specific score nudges) lives in
`lib/engagement/adapters/instagram.py`.

Cherry-pick of the top-N candidates for the comment queue is intentionally
still here — slice 3 of the OutboundEngagement refactor moves that into a
shared pipeline. IG comment quota stays at 2/day until then.

Usage:
    1. First time: python scripts/ig_login.py   (log in, save session)
    2. Then:       python scripts/ig_scan.py     (scan hashtags)
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

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
from lib.engagement.adapter import OutboundAdapter
from lib.engagement.adapters.instagram import InstagramHashtagAdapter
from lib.engagement.policy import EngagementPolicy
from lib.engagement.post import Post
from lib.logger import log_progress, log_step
from notifier import skill_finished, skill_skipped, skill_started
from rate_limiter import can_act, print_status, record_action, wait_random_delay

SESSION_FILE = settings.paths.instagram_session
QUEUE_FILE = settings.paths.comment_queue
LAST_RUN_FILE = settings.paths.last_run
ERROR_LOG = settings.paths.logs_dir / "errors.log"
CONFIG_FILE = settings.paths.brand_dir / "config.json"
HASHTAG_FILE = settings.paths.instagram_accounts


def load_config() -> dict[str, Any]:
    with CONFIG_FILE.open() as f:
        return json.load(f)


def log_error(msg: str) -> None:
    ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).isoformat()
    with ERROR_LOG.open("a") as f:
        f.write(f"[{ts}] {msg}\n")


def load_queue() -> list[dict[str, Any]]:
    if QUEUE_FILE.exists():
        with QUEUE_FILE.open() as f:
            return json.load(f)
    return []


def save_queue(queue: list[dict[str, Any]]) -> None:
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with QUEUE_FILE.open("w") as f:
        json.dump(queue, f, indent=2)


def load_last_run() -> dict[str, Any]:
    if LAST_RUN_FILE.exists():
        with LAST_RUN_FILE.open() as f:
            return json.load(f)
    return {}


def save_last_run(data: dict[str, Any]) -> None:
    LAST_RUN_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LAST_RUN_FILE.open("w") as f:
        json.dump(data, f, indent=2)


def _build_default_adapter(config: dict[str, Any]) -> InstagramHashtagAdapter:
    """Construct the production adapter wired to the scanner's settings paths."""
    adapter_config: dict[str, object] = {
        **config,
        "session_file": SESSION_FILE,
        "hashtag_file": HASHTAG_FILE,
        "headless": False,
    }
    return InstagramHashtagAdapter(adapter_config)


def _check_rerun_guard() -> bool:
    """Return True to continue, False to skip (already ran today)."""
    last_run = load_last_run()
    ig_last = last_run.get("ig_scanner", {})
    ig_last_date = (ig_last.get("last_run_at") or "")[:10]
    if ig_last_date == date.today().isoformat() and ig_last.get("status") == "success":
        msg = (
            f"Already ran today — liked {ig_last.get('posts_liked', 0)} posts, "
            f"queued {ig_last.get('posts_queued_for_comment', 0)} for comments"
        )
        print(f"SKIP: ig_scanner already ran successfully today ({ig_last_date}).")
        print("Use --force to override.")
        skill_skipped("ig-scanner", msg)
        if "--force" not in sys.argv:
            log_trace("instagram", "Skipped: already ran today")
            return False
        print("--force detected, re-running.\n")
    return True


def run_ig_scan(adapter: OutboundAdapter | None = None) -> int:
    """Orchestrate one IG hashtag scan run.

    Returns the number of posts queued for comments. The adapter parameter is
    DI for tests; production passes None and gets the real Playwright-backed
    InstagramHashtagAdapter.
    """
    print("=== Instagram Hashtag Scanner (CLI) ===\n", flush=True)
    log_trace("instagram", "Started Instagram hashtag scan")

    if not _check_rerun_guard():
        return 0

    if not can_act("instagram", "like"):
        print("ABORT: Daily IG like limit reached. Try again tomorrow.")
        log_trace("instagram", "Aborted: Daily like limit reached")
        skill_skipped("ig-scanner", "Daily IG like limit reached")
        print_status()
        return 0

    skill_started("ig-scanner", "Scanning Instagram hashtags for posts to like/comment")
    print_status()

    config = load_config()
    policy = EngagementPolicy.from_config(config)
    adapter_active = adapter if adapter is not None else _build_default_adapter(config)
    queue = load_queue()

    # Stats
    hashtags_scanned = 0
    posts_evaluated = 0
    posts_liked = 0
    posts_queued = 0
    posts_skipped_dedup = 0
    posts_skipped_score = 0
    posts_skipped_competitor = 0

    # Candidates collected during scan; top-N picked after the loop.
    comment_candidates: list[tuple[Post, float, str]] = []

    log_step("Launching browser")
    try:
        with adapter_active.session():
            log_step("Instagram session OK")
            sources = list(adapter_active.list_sources())
            print(f"Hashtags to scan today: {len(sources)}")
            for src in sources:
                category = getattr(src, "category", "?")
                print(f"  - {src.name} ({category})")
            print()

            if not sources:
                print("No hashtags scheduled for today. Done.")
                _persist_last_run(0, 0, 0, status="success")
                return 0

            for src_idx, source in enumerate(sources, 1):
                if not can_act("instagram", "like"):
                    print(
                        f"\nLike limit reached — stopping after {hashtags_scanned} hashtags.",
                        flush=True,
                    )
                    break

                category = getattr(source, "category", "general")
                log_progress(
                    src_idx, len(sources), f"Scanning: #{source.name}", f"category={category}"
                )
                print(f"    URL: {source.url}", flush=True)

                try:
                    hashtags_scanned += 1
                    for post in adapter_active.iterate_posts(source):
                        if not can_act("instagram", "like"):
                            print("    Like limit reached mid-scan.")
                            break

                        posts_evaluated += 1
                        result_counters = _process_post(
                            post=post,
                            adapter=adapter_active,
                            policy=policy,
                            comment_candidates=comment_candidates,
                        )
                        posts_liked += result_counters["liked"]
                        posts_skipped_dedup += result_counters["skipped_dedup"]
                        posts_skipped_score += result_counters["skipped_score"]
                        posts_skipped_competitor += result_counters["skipped_competitor"]

                        if result_counters["should_delay"]:
                            wait_random_delay("instagram", "like")
                except Exception as exc:
                    msg = f"Error scanning #{source.name}: {exc}"
                    print(f"    ERROR: {exc}")
                    log_error(msg)
                    continue
    except RuntimeError as exc:
        # Adapter raises this on missing session file / expired session.
        msg = str(exc)
        if "SESSION_EXPIRED" in msg or "No saved Instagram session" in msg:
            print(f"ABORT: {msg}")
            print("Re-run:  python scripts/ig_login.py")
            log_error(msg)
            log_trace("instagram", "Aborted: session expired or missing")
            return 0
        raise

    # Cherry-pick TOP-N (slice 3 will move this into a shared pipeline).
    posts_queued = _queue_top_candidates(
        candidates=comment_candidates,
        queue=queue,
        policy=policy,
    )
    save_queue(queue)

    _persist_last_run(hashtags_scanned, posts_liked, posts_queued, status="success")

    log_trace(
        "instagram",
        f"Scan complete: {hashtags_scanned} hashtags, {posts_liked} liked, {posts_queued} queued",
    )
    _print_summary(
        hashtags_scanned=hashtags_scanned,
        posts_evaluated=posts_evaluated,
        posts_liked=posts_liked,
        posts_queued=posts_queued,
        posts_skipped_dedup=posts_skipped_dedup,
        posts_skipped_score=posts_skipped_score,
        posts_skipped_competitor=posts_skipped_competitor,
        comment_candidates=comment_candidates,
    )
    print_status()
    summary = (
        f"📸 Hashtags scanned: {hashtags_scanned}\n"
        f"❤️ Posts liked: {posts_liked}/8\n"
        f"💬 Queued for comment: {posts_queued}/2\n"
        f"⏭️ Skipped: {posts_skipped_dedup} dedup, "
        f"{posts_skipped_score} low score, {posts_skipped_competitor} competitor"
    )
    skill_finished("ig-scanner", summary)
    return posts_queued


def _process_post(
    *,
    post: Post,
    adapter: OutboundAdapter,
    policy: EngagementPolicy,
    comment_candidates: list[tuple[Post, float, str]],
) -> dict[str, int]:
    """Score + filter + (maybe) like one post. Mutates comment_candidates.

    Returns counters: liked, skipped_dedup, skipped_score, skipped_competitor,
    should_delay (1 = caller should wait_random_delay, 0 = skip the wait).
    """
    counters = {
        "liked": 0,
        "skipped_dedup": 0,
        "skipped_score": 0,
        "skipped_competitor": 0,
        "should_delay": 0,
    }
    print(f"    Post {post.post_id[:12]}...", flush=True)

    if is_duplicate("instagram", post.post_id):
        counters["skipped_dedup"] = 1
        print("        SKIP: already engaged", flush=True)
        return counters

    rejection = adapter.pre_filter(post)
    if rejection is not None:
        if rejection == "competitor":
            counters["skipped_competitor"] = 1
            print("        SKIP: competitor account")
        elif rejection == "own_account":
            print("        SKIP: own account")
        elif rejection == "too_old":
            weeks_old = float(post.platform_extra.get("weeks_old", 0) or 0)
            print(f"        SKIP: post too old ({weeks_old:.0f}w)")
            counters["skipped_score"] = 1
        else:
            print(f"        SKIP: {rejection}")
            counters["skipped_score"] = 1
        return counters

    like_count = int(post.platform_extra.get("like_count", 0) or 0)
    comment_count = int(post.platform_extra.get("comment_count", 0) or 0)
    weeks_old = float(post.platform_extra.get("weeks_old", 0) or 0)
    snippet = post.text[:60].replace("\n", " ")
    print(f"    @{post.author or '?'}: {snippet}...")
    print(f"        likes~{like_count} comments~{comment_count} age~{weeks_old:.1f}w")

    base_score = score_relevance(post.text, {"comment_count": comment_count, "hours_old": 12})
    score = adapter.adjust_score(post, base_score)
    print(f"        score={score} (threshold={policy.candidate_threshold})")

    if not policy.is_candidate(score):
        counters["skipped_score"] = 1
        return counters

    # Inline like.
    like_result = adapter.like(post)
    if like_result.liked:
        record_action("instagram", "like")
        mark_engaged("instagram", post.post_id, "like", post.source_name or "")
        counters["liked"] = 1
        print("        LIKED")
    elif like_result.reason.startswith("skipped:already_liked"):
        print("        already liked")
    else:
        print(f"        like: {like_result.reason}")
        log_error(f"LIKE_FAILED: {post.post_id} reason={like_result.reason}")

    # Collect comment candidate (cherry-pick happens after the loop).
    if policy.is_comment_candidate(score) and "?" in post.text:
        category = str(post.platform_extra.get("category", "general"))
        comment_candidates.append((post, score, category))

    counters["should_delay"] = 1
    return counters


def _queue_top_candidates(
    *,
    candidates: list[tuple[Post, float, str]],
    queue: list[dict[str, Any]],
    policy: EngagementPolicy,
) -> int:
    """Sort candidates by score desc, queue up to (daily quota - already queued today).

    Drafts each comment, builds the queue record via Post.to_queue_record (IG always
    requires approval), and appends in-place to `queue`. Returns count queued.
    """
    today_iso = date.today().isoformat()
    existing_ig_today = sum(
        1
        for q in queue
        if q.get("platform") == "instagram"
        and q.get("queued_at", "").startswith(today_iso)
    )
    quota = policy.daily_comment_quota.get("instagram", 2)
    budget = max(0, quota - existing_ig_today)
    if budget == 0:
        return 0

    selected = sorted(candidates, key=lambda c: c[1], reverse=True)[:budget]
    queued = 0
    for post, score, _category in selected:
        draft = draft_comment_for_post(
            platform="instagram",
            post_text=post.text[:600],
            group_or_hashtag=post.source_name,
            post_url=post.post_url,
        )
        if not draft:
            log.info(
                {
                    "event": "draft_inline_empty",
                    "platform": "instagram",
                    "post_url": post.post_url,
                }
            )
        record = post.to_queue_record(
            score=score,
            draft=draft,
            requires_approval=True,  # IG always requires approval
            queued_at=datetime.now(UTC).isoformat(),
        )
        queue.append(record)
        queued += 1
        print(
            f"\nQUEUED for comment: @{post.author or '?'} "
            f"score={score} #{post.source_name}"
        )
    return queued


def _persist_last_run(
    hashtags_scanned: int,
    posts_liked: int,
    posts_queued: int,
    *,
    status: str,
) -> None:
    last_run = load_last_run()
    last_run["ig_scanner"] = {
        "last_run_at": datetime.now(UTC).isoformat(),
        "hashtags_scanned": hashtags_scanned,
        "posts_liked": posts_liked,
        "posts_queued_for_comment": posts_queued,
        "status": status,
    }
    save_last_run(last_run)


def _print_summary(
    *,
    hashtags_scanned: int,
    posts_evaluated: int,
    posts_liked: int,
    posts_queued: int,
    posts_skipped_dedup: int,
    posts_skipped_score: int,
    posts_skipped_competitor: int,
    comment_candidates: list[tuple[Post, float, str]],
) -> None:
    print(f"""
=== Instagram Scan Complete ===
Hashtags scanned today: {hashtags_scanned}
Posts evaluated: {posts_evaluated}
Posts liked: {posts_liked} / 8 daily limit
Posts queued for comments: {posts_queued} / 2 daily limit
Posts skipped — already engaged: {posts_skipped_dedup}
Posts skipped — below threshold: {posts_skipped_score}
Posts skipped — competitor account: {posts_skipped_competitor}
""")

    if comment_candidates:
        print("Top comment candidates:")
        sorted_cands = sorted(comment_candidates, key=lambda c: c[1], reverse=True)
        for i, (post, score, _cat) in enumerate(sorted_cands[:5], 1):
            snip = post.text[:50].replace("\n", " ")
            print(
                f'  {i}. @{post.author or "?"} — "{snip}..." '
                f"(score: {score}) — #{post.source_name}"
            )
        print()


if __name__ == "__main__":
    run_ig_scan()
