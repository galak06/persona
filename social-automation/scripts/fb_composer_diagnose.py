"""One-shot diagnostic: open a group's composer and dump the dialog buttons.

Tells us exactly what aria-labels / textContent / attributes the Post button
uses today so we can fix the submit selector in fb_group_post.py.

Safe: opens the composer, dumps the DOM, then exits. Never types, never posts.

Usage:
    python scripts/fb_composer_diagnose.py <group_url>
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SESSION_FILE = PROJECT_ROOT / ".claude/state/facebook_session.json"


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: fb_composer_diagnose.py <group_url>")
        sys.exit(2)
    url = sys.argv[1]

    from playwright.sync_api import sync_playwright

    ua = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(
            storage_state=str(SESSION_FILE),
            viewport={"width": 1280, "height": 900},
            user_agent=ua,
        )
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(4)

        clicked = page.evaluate(
            """() => {
            const nodes = Array.from(document.querySelectorAll('[role="button"], div, span'));
            const placeholder = nodes.find(el => {
                const t = (el.textContent || '').trim().toLowerCase();
                return t === 'write something…' || t.startsWith('write something') ||
                       t === 'create a public post…' || t.startsWith('write a post');
            });
            if (placeholder) { placeholder.click(); return 'clicked'; }
            return 'not_found';
        }"""
        )
        print(f"composer-open: {clicked}")
        time.sleep(4)

        # Type a tiny bit of filler so the Post button enables (some FB UIs
        # keep the submit disabled until the composer has content).
        editable = page.query_selector('[contenteditable="true"][data-lexical-editor="true"]')
        if editable:
            editable.click()
            time.sleep(0.5)
            page.keyboard.type("test", delay=30)
            time.sleep(1.5)
            print("typed: 'test' (so Post button enables)")
        else:
            print("typed: (composer contenteditable not found — skipping)")

        dump = page.evaluate(
            """() => {
            const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
            // Find the composer contenteditable, walk up to a reasonable ancestor,
            // then dump every button inside that subtree.
            const box = document.querySelector('[contenteditable="true"][data-lexical-editor="true"]')
                || document.querySelector('[contenteditable="true"][role="textbox"]');
            if (!box) return {status: 'no_composer'};
            // Walk 6 levels up and find the ancestor that contains any Post-like button.
            let anchor = box;
            for (let i = 0; i < 8; i++) {
                if (anchor.parentElement) anchor = anchor.parentElement;
            }
            const btns = Array.from(anchor.querySelectorAll('[role="button"], button'));
            const out = btns.map(b => ({
                tag: b.tagName.toLowerCase(),
                aria_label: b.getAttribute('aria-label'),
                aria_disabled: b.getAttribute('aria-disabled'),
                disabled: b.disabled || null,
                text: norm(b.textContent).slice(0, 80),
                rect_w: Math.round(b.getBoundingClientRect().width),
            }));
            return {status: 'ok', count: btns.length, buttons: out, anchor_tag: anchor.tagName.toLowerCase()};
        }"""
        )

        print(f"\n=== DIALOG BUTTONS ({dump.get('status')}, count={dump.get('count')}) ===")
        for i, b in enumerate(dump.get("buttons", [])):
            print(
                f"  [{i:02d}] aria_label={b['aria_label']!r:<30} "
                f"disabled={b['aria_disabled']} "
                f"text={b['text']!r:<40} w={b['rect_w']}"
            )

        ctx.storage_state(path=str(SESSION_FILE))
        ctx.close()
        browser.close()


if __name__ == "__main__":
    main()
