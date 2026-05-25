# pyright: reportMissingImports=false
"""Per-group browser publish for FB group posts.

Extracted from `scripts/fb_group_post.py` so the main script stays under
the 300-line cap after the slice-5 refactor (approval-first sequencing +
per-group browser lifecycle).

Owns the entire short-lived Playwright session: open Chromium → load FB
group → drive composer → optionally attach reel → submit (or no-submit
screenshot) → close. One call = one group = one browser session, per the
approval-before-browser rule (see feedback_approval_before_browser.md).

The `open_composer_and_post` function (the actual composer JS) still lives
in `scripts/fb_group_post.py` and is passed in as `composer_fn` — keeps
the long page.evaluate(...) blocks in one place while letting this module
own the lifecycle.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lib.local_env import get_runtime_headless

logger = logging.getLogger(__name__)


def publish_to_group(
    *,
    group_url: str,
    session_file: Path,
    composer_fn: Callable[..., tuple[str, str | None]],
    caption: str,
    link_for_comment: str | None,
    reel_path: Path | None,
    reel_thumbnail: Path | None,
    no_submit: bool,
    screenshot_path: Path | None,
) -> tuple[str, str | None]:
    """Open a fresh Chromium context, drive `composer_fn`, close.

    Returns (status, permalink) from `composer_fn`:
      - status: one of "posted" / "pending_admin_approval" /
        "submit_unverified" / "failed"
      - permalink: the live FB URL of the just-published post when
        status=="posted" and the verifier scraped one; otherwise None
    Pass-through; this module owns the browser lifecycle only.

    `composer_fn` is invoked with the live Playwright `page` plus all the
    publish kwargs — it owns the FB DOM-driving JS (composer scoping,
    contenteditable selection, submit click, /my_posted_content verifier).
    This module owns only the browser lifecycle so the per-group
    "open → act → close" rule is enforceable in one place.
    """
    from playwright.sync_api import sync_playwright

    if not session_file.exists():
        raise RuntimeError("FB session missing — run fb_login.py first")

    ua = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=get_runtime_headless())
        ctx = browser.new_context(
            storage_state=str(session_file),
            viewport={"width": 1280, "height": 900},
            user_agent=ua,
        )
        try:
            page = ctx.new_page()
            page.goto("https://www.facebook.com", wait_until="domcontentloaded")
            time.sleep(2)
            if "login" in page.url.lower():
                raise RuntimeError("FB session expired")

            result = composer_fn(
                page,
                group_url,
                caption,
                link_for_comment,
                reel_path=reel_path,
                reel_thumbnail=reel_thumbnail,
                no_submit=no_submit,
                screenshot_path=screenshot_path,
            )
            # Defensive: composer_fn must return (status, permalink). Normalize
            # any stray bare-string return so callers always get a tuple.
            if isinstance(result, tuple):
                status = str(result[0])
                permalink = result[1] if len(result) > 1 else None
            else:
                status = str(result)
                permalink = None
        finally:
            # Persist any cookie refresh, then tear down hard. We open per
            # group; closing immediately keeps the session short (FB
            # flags long-idle Chromium sessions as suspicious).
            try:
                ctx.storage_state(path=str(session_file))
            except Exception as e:
                # Best-effort cookie persist — if it fails the next run
                # will just re-use the previous session state.
                logger.warning("session-persist failed: %s", e)
            ctx.close()
            browser.close()

    return status, permalink


def assert_create_post_dialog(page: Any) -> None:
    """Defensive Python-side check that the open [role="dialog"] is the
    create-post composer, not a stray dialog (comment box overlay, settings
    flyout, etc).

    Raises ``RuntimeError`` with a clear message when the assertion fails —
    the caller MUST NOT type into the composer if this raises (that's how
    the May 2026 bug published comments instead of posts).

    Checks across all open ``[role="dialog"]`` nodes:
      1. At least one dialog is visible.
      2. The dialog (or one of its descendants) carries an aria-label OR
         aria-placeholder matching the create-post intent
         (``create post``, ``create.*public.*post``, ``write something``,
         ``what's on your mind``).
      3. The descendant ``[contenteditable]`` we'd type into does NOT have
         an aria-label/placeholder starting with ``Comment``.
    """
    result = page.evaluate(
        """() => {
        const dialogs = Array.from(document.querySelectorAll('[role="dialog"]'));
        if (dialogs.length === 0) return {ok: false, reason: 'no_dialog'};

        const PAT = /(create.*post|write\\s*something|what.?s on your mind)/i;
        let intent_found = false;
        let bad_editor = null;
        let editor_label = '';

        for (const dlg of dialogs) {
            const dlgLabel = (dlg.getAttribute('aria-label') || '');
            if (PAT.test(dlgLabel)) intent_found = true;

            // Scan child elements for aria-label / aria-placeholder hits.
            const labelled = dlg.querySelectorAll('[aria-label], [aria-placeholder]');
            for (const el of labelled) {
                const al = el.getAttribute('aria-label') || '';
                const ap = el.getAttribute('aria-placeholder') || '';
                if (PAT.test(al) || PAT.test(ap)) intent_found = true;
            }

            // Find the editable we'd actually type into and guard against
            // "Comment" surfaces.
            const editable = dlg.querySelector('[contenteditable="true"]');
            if (editable) {
                const al = (editable.getAttribute('aria-label') || '').toLowerCase();
                const ap = (editable.getAttribute('aria-placeholder') || '').toLowerCase();
                editor_label = al || ap || '(none)';
                if (al.startsWith('comment') || ap.startsWith('comment')) {
                    bad_editor = editor_label;
                }
            }
        }

        if (bad_editor) return {ok: false, reason: 'editor_is_comment', editor_label: bad_editor};
        if (!intent_found) return {ok: false, reason: 'no_create_post_intent', editor_label};
        return {ok: true, editor_label};
    }"""
    )

    if not result.get("ok"):
        raise RuntimeError(
            "Create-post dialog assertion failed: "
            f"reason={result.get('reason')} "
            f"editor_label={result.get('editor_label', '?')!r} — "
            "refusing to type (would silently post a comment instead of a "
            "post; see feedback_approval_before_browser.md / May 2026 bug)"
        )
