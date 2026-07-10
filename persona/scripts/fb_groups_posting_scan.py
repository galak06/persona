"""Classify each tracked FB group's posting mode without posting anything.

For every entry in `data/groups_tracker.json`, opens the group URL, inspects
the top-of-feed composer area, and sets `posting_mode` based on what it sees:

  - admins_only     — no composer visible anywhere at the top (members can't post)
  - admin_approval  — composer visible + a "reviewed by admins" notice nearby
  - direct          — composer visible, no review notice
  - unknown         — ambiguous; keep as-is

Also records member count + URL. Safe: read-only, no clicks beyond scroll.

Usage:
    python -m scripts.fb_groups_posting_scan
    python -m scripts.fb_groups_posting_scan --only 398460282269029
    python -m scripts.fb_groups_posting_scan --dry-run
    python -m scripts.fb_groups_posting_scan --health-check
"""

from __future__ import annotations

import argparse
import sys
import time
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
from notifier import skill_error, skill_finished, skill_started

if TYPE_CHECKING:
    from playwright.sync_api import Page

SKILL_NAME = "fb-groups-posting-scan"

if settings.paths is None:
    raise RuntimeError("settings.paths is unset; lib.config failed to resolve BRAND_DIR")
from lib import groups_db  # FB groups live in groups.db (was groups_tracker.json)


def _classify(page: Page) -> dict[str, Any]:
    """Return {posting_mode, evidence} after inspecting the current group page."""
    time.sleep(4)
    # Nudge the top-of-feed composer into view.
    page.evaluate("window.scrollTo(0, 0)")
    time.sleep(1)
    result: dict[str, Any] = page.evaluate(
        """() => {
        const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
        // Grab the first ~3000 chars of visible text near the top — that covers
        // the group header + composer without pulling full feed noise.
        const topText = norm(document.body.innerText).slice(0, 3000).toLowerCase();

        // Is there a post composer placeholder anywhere visible in the top region?
        const placeholderSelectors = [
            '[role="button"]',
            'div[data-pagelet*="GroupFeed"]',
        ];
        const placeholders = Array.from(document.querySelectorAll('[role="button"], div, span'));
        const hasComposer = placeholders.some(el => {
            const t = norm(el.textContent || '').toLowerCase();
            if (!t) return false;
            return (
                t === 'write something…' ||
                t.startsWith('write something') ||
                t === 'create a public post…' ||
                t.startsWith('create a public post') ||
                t.startsWith('write a post')
            );
        });

        // Admin-approval signals — text that only appears when posts are moderated.
        const approvalHints = [
            'admins review posts',
            'admins must approve',
            'your post will be reviewed',
            'reviewed by admins',
            'posts in this group are reviewed',
        ];
        const approvalHit = approvalHints.find(h => topText.includes(h));

        let mode;
        if (!hasComposer) mode = 'admins_only';
        else if (approvalHit) mode = 'admin_approval';
        else mode = 'direct';

        return {
            posting_mode: mode,
            has_composer: hasComposer,
            approval_hint: approvalHit || null,
        };
    }"""
    )
    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Classify posting_mode for tracked FB groups"
    )
    parser.add_argument("--only", help="limit to a single group id (digits from URL)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="scan only, don't write the tracker",
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
            f"SESSION_EXPIRED: {session.storage_path} missing or empty",
            file=sys.stderr,
        )
        return 1
    print(f"FB session OK (storage: {session.storage_path})")
    return 0


def main(
    session: FbSession,
    *,
    only: str | None = None,
    dry_run: bool = False,
) -> int:
    skill_started(SKILL_NAME, "classifying posting_mode for tracked groups")

    if not session.is_authenticated():
        skill_error(SKILL_NAME, "FB session not found — run fb_login.py")
        return 1

    tracker: list[dict[str, Any]] = groups_db.load_all()
    targets = [g for g in tracker if not only or only in g.get("group_url", "")]
    print(f"scanning {len(targets)} group(s)…", flush=True)

    updated = 0
    with session.page() as page:
        for group in targets:
            url = group["group_url"]
            log_step(f"  → {group['group_name'][:45]}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                result = _classify(page)
            except Exception as e:
                print(f"    ERROR: {e}", flush=True)
                continue
            mode = result["posting_mode"]
            hint = result.get("approval_hint")
            hint_str = f" (hint: {hint!r})" if hint else ""
            # Respect human-set flags — `blocked` and `links_blocked` are
            # semantic tags (off-brand, spam-flagged) that a capability
            # scan can't re-derive. Never overwrite them.
            current = group.get("posting_mode")
            if current in {"blocked", "links_blocked"}:
                print(
                    f"    → {mode}{hint_str} "
                    f"(keeping manual {current!r}, would have set {mode!r})",
                    flush=True,
                )
                append_group_note(
                    group,
                    f"Auto-scan saw {mode}; kept manual {current}.",
                )
                continue
            print(f"    → {mode}{hint_str}", flush=True)
            group["posting_mode"] = mode
            note_text = f"Auto-classified {mode}" + (
                f" — hint: {hint}" if hint else ""
            )
            append_group_note(group, note_text)
            updated += 1
            time.sleep(1.5)

    if dry_run:
        print("\n(dry-run — tracker not written)", flush=True)
    else:
        groups_db.save_all(tracker)
    skill_finished(SKILL_NAME, f"classified {updated}/{len(targets)} groups")
    print(f"\n=== Done === classified {updated}/{len(targets)} groups", flush=True)
    return 0


if __name__ == "__main__":
    args = _parse_args()
    fb_session = build_fb_session()

    if args.health_check:
        sys.exit(_health_check(fb_session))

    try:
        with SingletonLock(SKILL_NAME):
            sys.exit(main(fb_session, only=args.only, dry_run=args.dry_run))
    except LockAcquisitionError as exc:
        print(f"another instance of {SKILL_NAME!r} is running: {exc}", file=sys.stderr)
        sys.exit(0)
