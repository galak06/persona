"""One-shot DOM diagnostic for FB group pages.

Opens a group URL, dumps the text patterns we need to enrich:
  - all headings (h1/h2/h3/role=heading) with their textContent
  - every element whose text contains 'member', 'private', 'public', or 'rule'
  - the full sidebar region (usually holds member count + rules card)

Used to iterate the selectors in fb_group_enrich.py without re-running the
full tracker loop. Run against one group, update selectors, re-test.

Usage:
    python scripts/fb_group_diagnose.py <group_url>
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SESSION_FILE = settings.paths.facebook_session


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: fb_group_diagnose.py <group_url>")
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
        time.sleep(5)
        for pct in (0.25, 0.5, 0.75):
            page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {pct})")
            time.sleep(1.2)
        page.evaluate("window.scrollTo(0, 0)")
        time.sleep(1.5)

        data = page.evaluate(
            """() => {
            const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();

            // All headings
            const headings = Array.from(document.querySelectorAll(
                'h1, h2, h3, [role="heading"]'
            )).map(el => ({
                tag: el.tagName.toLowerCase(),
                level: el.getAttribute('aria-level') || '',
                text: norm(el.textContent).slice(0, 200),
            })).filter(h => h.text);

            // Elements mentioning keywords
            const KEYS = ['private group', 'public group', 'members', ' rule', 'rules', 'posts a day'];
            const kwHits = [];
            const seen = new Set();
            const elements = Array.from(document.querySelectorAll(
                'div, span, a, section'
            ));
            for (const el of elements) {
                const t = norm(el.textContent);
                if (!t || t.length > 500) continue;
                const lo = t.toLowerCase();
                const hit = KEYS.find(k => lo.includes(k));
                if (!hit) continue;
                if (seen.has(t)) continue;
                seen.add(t);
                kwHits.push({ keyword: hit, tag: el.tagName.toLowerCase(), text: t.slice(0, 200) });
                if (kwHits.length > 40) break;
            }

            return { title: document.title, url: location.href, headings, kwHits };
        }"""
        )

        ctx.storage_state(path=str(SESSION_FILE))
        ctx.close()
        browser.close()

    print(f"\n=== URL: {data['url']} ===")
    print(f"TITLE: {data['title']}\n")
    print("--- HEADINGS ---")
    for h in data["headings"][:30]:
        print(f"  <{h['tag']}{' level=' + h['level'] if h['level'] else ''}> {h['text']}")
    print("\n--- KEYWORD HITS (member / private / public / rules) ---")
    for kw in data["kwHits"][:40]:
        print(f"  [{kw['keyword']:<15}] <{kw['tag']}> {kw['text']}")


if __name__ == "__main__":
    main()
