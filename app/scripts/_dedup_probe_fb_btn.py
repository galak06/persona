"""Probe FB comment-row button anatomy."""
from __future__ import annotations
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from lib.local_env import get_runtime_headless

FB_SESSION = PROJECT_ROOT / ".claude" / "state" / "facebook_session.json"
URL = "https://www.facebook.com/groups/560658691507853/posts/1946255526281489/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
DOM_JS = (Path(__file__).resolve().parent / "_dedup_dom.js").read_text()


def main():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=get_runtime_headless())
        ctx = browser.new_context(storage_state=str(FB_SESSION), viewport={"width": 1280, "height": 900}, user_agent=UA)
        page = ctx.new_page()
        page.goto("https://www.facebook.com", wait_until="domcontentloaded")
        time.sleep(3)
        page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        time.sleep(8)
        for pct in (0.4, 0.7, 1.0):
            page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {pct})")
            time.sleep(2)
        page.evaluate(DOM_JS)
        articles = page.evaluate("() => window.loadAllFbComments()")
        print(f"after expand: {articles} articles", flush=True)
        # Now inspect the matching comment row
        info = page.evaluate("""
        (target) => {
            const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
            const articles = Array.from(document.querySelectorAll('div[role="article"]'));
            const matching = articles.filter(a => {
                const aria = (a.getAttribute('aria-label') || '').toLowerCase();
                return aria.startsWith('comment by') && norm(a.innerText).includes(target);
            });
            if (matching.length === 0) return { matched: 0 };
            const row = matching[0];
            row.scrollIntoView({block: 'center'});
            row.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
            row.dispatchEvent(new MouseEvent('mouseenter', {bubbles: true}));
            // Find ALL clickable elements within
            const all = row.querySelectorAll('[role="button"], button, [aria-haspopup], div[tabindex]');
            const dump = [];
            for (const el of all) {
                dump.push({
                    tag: el.tagName,
                    role: el.getAttribute('role'),
                    aria: (el.getAttribute('aria-label') || '').slice(0, 80),
                    haspopup: el.getAttribute('aria-haspopup'),
                    tabindex: el.getAttribute('tabindex'),
                    text: norm(el.textContent).slice(0, 50),
                    rect: el.getBoundingClientRect().width,
                });
            }
            return { matched: matching.length, rowAria: row.getAttribute('aria-label'), buttonCount: all.length, dump: dump.slice(0, 30) };
        }""", "Balancing calcium is honestly the trickiest part")
        print(f"\nmatched: {info.get('matched')}")
        print(f"rowAria: {info.get('rowAria')}")
        print(f"buttonCount: {info.get('buttonCount')}")
        for d in info.get('dump', []):
            print(f"  <{d['tag'].lower()}> role={d['role']!r:<8} aria='{d['aria']}' haspopup={d['haspopup']} tabindex={d['tabindex']} text='{d['text']}' w={d['rect']:.0f}")
        ctx.close()
        browser.close()


if __name__ == "__main__":
    main()
