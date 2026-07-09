"""Check on FB group posts stuck in admin-approval queue.

Revisits every group in `data/groups_tracker.json` with last_post_status=pending_approval,
looks for our caption in the group's feed, and updates status accordingly:

  - If found in feed → status=posted, saves the permalink, prints a
    "⏰ ADD FIRST COMMENT NOW" reminder with the URL + permalink for you to
    paste manually (comment automation is reserved for another pass).
  - If still not visible after N days (configurable) → status=stale_pending,
    appends a note suggesting manual follow-up with the group admins.
  - Otherwise → leaves pending, bumps last_checked timestamp.

Safe: navigation + read-only scraping. No posting.

Usage:
    python -m scripts.fb_pending_posts_check                  # check every pending entry
    python -m scripts.fb_pending_posts_check --only 398460282269029
    python -m scripts.fb_pending_posts_check --stale-days 3   # flag posts pending > 3d
    python -m scripts.fb_pending_posts_check --dry-run        # scan only, no tracker write
    python -m scripts.fb_pending_posts_check --health-check   # verify session + exit
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from lib.bootstrap import init_script

settings, log = init_script(__name__)

from lib.fb.session import FbSession, build_fb_session
from lib.groups.notes import append_group_note
from lib.logger import log_step
from lib.runtime.singleton import LockAcquisitionError, SingletonLock
from notifier import send, skill_error, skill_finished, skill_started

if TYPE_CHECKING:
    from playwright.sync_api import Page

SKILL_NAME = "fb-pending-posts-check"

if settings.paths is None:
    raise RuntimeError("settings.paths is unset; lib.config failed to resolve BRAND_DIR")
from lib import groups_db  # FB groups live in groups.db (was groups_tracker.json)

_MATCH_PREFIX_LEN = 40


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _scan_feed_for_caption(page: Page, caption_prefix: str) -> dict[str, Any]:
    """Return {found, permalink, pending_banner} for the given caption prefix."""
    time.sleep(4)
    for pct in (0.3, 0.6):
        page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {pct})")
        time.sleep(1.2)
    page.evaluate("window.scrollTo(0, 0)")
    time.sleep(1.5)
    result: dict[str, Any] = page.evaluate(
        """(prefix) => {
        const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
        const needle = norm(prefix).toLowerCase();
        // Scan articles (comments use role=article too but posts in group feed
        // are the outermost article containers). Find the shortest one
        // that contains our prefix — the innermost post wrapper.
        const articles = Array.from(document.querySelectorAll('[role="article"]'));
        const hits = [];
        for (const el of articles) {
            const t = norm(el.textContent).toLowerCase();
            if (t.indexOf(needle) !== -1) hits.push({el, len: t.length});
        }
        hits.sort((a, b) => a.len - b.len);
        const post = hits.length ? hits[0].el : null;
        let permalink = null;
        if (post) {
            // FB permalinks usually sit on a timestamp-like anchor inside the post.
            const anchors = Array.from(post.querySelectorAll('a[href*="/posts/"], a[href*="/permalink/"]'));
            for (const a of anchors) {
                if (a.href) { permalink = a.href.split('?')[0]; break; }
            }
        }
        // Also check for a top-of-page pending banner, which some groups show
        // until your post is reviewed.
        const body = norm(document.body.innerText).toLowerCase().slice(0, 4000);
        const pendingBanner = (
            body.includes('pending review') ||
            body.includes('submitted to group admins for approval') ||
            body.includes('your post is pending')
        );
        return { found: !!post, permalink, pending_banner: pendingBanner };
    }""",
        caption_prefix,
    )
    return result


def _process(page: Page, entry: dict[str, Any], stale_days: int) -> str:
    """Return one of: posted / still_pending / stale_pending / no_caption."""
    last_caption = entry.get("last_post_caption") or ""
    if not last_caption:
        # Without the caption text there's nothing to match in the feed.
        return "no_caption"
    prefix = last_caption[:_MATCH_PREFIX_LEN]
    page.goto(entry["group_url"], wait_until="domcontentloaded", timeout=30000)
    try:
        result = _scan_feed_for_caption(page, prefix)
    except Exception as e:
        log_step(f"    scan error: {e}")
        return "still_pending"

    now = _now_iso()
    entry["last_checked_at"] = now

    if result["found"]:
        entry["last_post_status"] = "posted"
        if result["permalink"]:
            entry["last_post_permalink"] = result["permalink"]
        append_group_note(entry, "Pending post now visible in feed.")
        return "posted"

    pending_since_str = entry.get("last_post_at") or now
    try:
        pending_since = datetime.fromisoformat(pending_since_str.replace("Z", "+00:00"))
    except ValueError:
        pending_since = datetime.now(UTC)
    age_days = (datetime.now(UTC) - pending_since).days
    if age_days >= stale_days:
        entry["last_post_status"] = "stale_pending"
        append_group_note(
            entry,
            f"Still not visible after {age_days}d — likely declined by admins.",
        )
        return "stale_pending"
    return "still_pending"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check on pending-approval FB group posts")
    parser.add_argument("--only", help="group id to check (digits from URL)")
    parser.add_argument(
        "--stale-days",
        type=int,
        default=5,
        help="flag as stale after N days",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="scan only, don't write the tracker or send reminders",
    )
    parser.add_argument(
        "--health-check",
        action="store_true",
        help="verify FB session is authenticated and exit",
    )
    return parser.parse_args()


def _health_check(session: FbSession) -> int:
    if not session.is_authenticated():
        print(
            f"SESSION_EXPIRED: {session.storage_path}",
            file=sys.stderr,
        )
        return 1
    print(f"FB session OK (storage: {session.storage_path})")
    return 0


def main(
    session: FbSession,
    *,
    dry_run: bool = False,
    only: str | None = None,
    stale_days: int = 5,
) -> int:
    skill_started(SKILL_NAME, "checking admin-approval queue")

    if not session.is_authenticated():
        skill_error(SKILL_NAME, "FB session missing — run fb_login.py")
        return 1

    tracker: list[dict[str, Any]] = groups_db.load_all()
    pool = [
        e
        for e in tracker
        if e.get("last_post_status") == "pending_approval"
        and (not only or only in e.get("group_url", ""))
    ]
    if not pool:
        msg = "no pending posts to check"
        print(msg, flush=True)
        skill_finished(SKILL_NAME, msg)
        return 0

    print(f"checking {len(pool)} pending post(s)…", flush=True)

    posted = still = stale = no_cap = 0
    reminders: list[str] = []
    with session.page() as page:
        for entry in pool:
            log_step(f"  → {entry['group_name'][:45]}")
            outcome = _process(page, entry, stale_days)
            print(f"    → {outcome}", flush=True)
            if outcome == "posted":
                posted += 1
                permalink = entry.get("last_post_permalink", entry["group_url"])
                reminders.append(
                    f"• {entry['group_name']}\n  Add URL comment on: {permalink}"
                )
            elif outcome == "still_pending":
                still += 1
            elif outcome == "stale_pending":
                stale += 1
            else:
                no_cap += 1

    if dry_run:
        print("\n(dry-run — tracker not written, reminders suppressed)", flush=True)
    else:
        groups_db.save_all(tracker)

        if reminders:
            print("\n⏰ ADD FIRST COMMENT NOW on these approved posts:", flush=True)
            for r in reminders:
                print(r, flush=True)
            send(
                "⏰ <b>Reel pipeline: approved posts ready for URL comment</b>\n\n"
                + "\n".join(reminders)[:2000]
            )

    summary = f"posted={posted} still_pending={still} stale={stale} no_caption={no_cap}"
    print(f"\n=== Done === {summary}", flush=True)
    skill_finished(SKILL_NAME, summary)
    return 0


if __name__ == "__main__":
    args = _parse_args()
    fb_session = build_fb_session()

    if args.health_check:
        sys.exit(_health_check(fb_session))

    try:
        with SingletonLock(SKILL_NAME):
            sys.exit(
                main(
                    fb_session,
                    dry_run=args.dry_run,
                    only=args.only,
                    stale_days=args.stale_days,
                )
            )
    except LockAcquisitionError as exc:
        print(f"another instance of {SKILL_NAME!r} is running: {exc}", file=sys.stderr)
        sys.exit(0)
