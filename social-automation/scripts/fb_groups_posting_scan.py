"""Classify each tracked FB group's posting mode without posting anything.

For every entry in `data/groups_tracker.json`, opens the group URL, inspects
the top-of-feed composer area, and sets `posting_mode` based on what it sees:

  - admins_only     — no composer visible anywhere at the top (members can't post)
  - admin_approval  — composer visible + a "reviewed by admins" notice nearby
  - direct          — composer visible, no review notice
  - unknown         — ambiguous; keep as-is

Also records member count + URL. Safe: read-only, no clicks beyond scroll.

Usage:
    python scripts/fb_groups_posting_scan.py
    python scripts/fb_groups_posting_scan.py --only 398460282269029
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from lib.bootstrap import init_script
settings, log = init_script(__name__)

from lib.local_env import get_runtime_headless
from lib.logger import log_step
from notifier import skill_error, skill_finished, skill_started


SESSION_FILE = settings.paths.facebook_session
TRACKER_FILE = settings.paths.groups_tracker


def _classify(page) -> dict:
    """Return {posting_mode, evidence} after inspecting the current group page."""
    time.sleep(4)
    # Nudge the top-of-feed composer into view.
    page.evaluate("window.scrollTo(0, 0)")
    time.sleep(1)
    return page.evaluate(
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify posting_mode for tracked FB groups")
    parser.add_argument("--only", help="limit to a single group id (digits from URL)")
    args = parser.parse_args()

    skill_started("fb-groups-posting-scan", "classifying posting_mode for tracked groups")

    if not SESSION_FILE.exists():
        skill_error("fb-groups-posting-scan", "FB session not found — run fb_login.py")
        return
    tracker = json.loads(TRACKER_FILE.read_text())
    targets = [g for g in tracker if not args.only or args.only in g.get("group_url", "")]
    print(f"scanning {len(targets)} group(s)…", flush=True)

    from playwright.sync_api import sync_playwright

    ua = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    updated = 0
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=get_runtime_headless())
        ctx = browser.new_context(
            storage_state=str(SESSION_FILE),
            viewport={"width": 1280, "height": 900},
            user_agent=ua,
        )
        page = ctx.new_page()
        try:
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
                    group.setdefault("notes", []).append(
                        {"at": now, "text": f"Auto-scan saw {mode}; kept manual {current}."}
                    )
                    continue
                print(f"    → {mode}{hint_str}", flush=True)
                # Backfill new fields if missing.
                group.setdefault("notes", [])
                group["posting_mode"] = mode
                note_text = f"Auto-classified {mode}" + (f" — hint: {hint}" if hint else "")
                group["notes"].append({"at": now, "text": note_text})
                updated += 1
                time.sleep(1.5)
        finally:
            ctx.storage_state(path=str(SESSION_FILE))
            ctx.close()
            browser.close()

    TRACKER_FILE.write_text(json.dumps(tracker, indent=2))
    skill_finished("fb-groups-posting-scan", f"classified {updated}/{len(targets)} groups")
    print(f"\n=== Done === classified {updated}/{len(targets)} groups", flush=True)


if __name__ == "__main__":
    main()
