"""Facebook group search + join-button actions via Playwright."""

from __future__ import annotations

import random
import time

EXTRACT_GROUP_CARDS_JS = """
() => {
    const cards = [];
    const links = Array.from(document.querySelectorAll('a[href*="/groups/"]'));
    const seen = new Set();

    for (const a of links) {
        const href = a.getAttribute('href') || '';
        const match = href.match(/\\/groups\\/([^/?#]+)/);
        if (!match) continue;
        const gid = match[1];
        if (['feed', 'discover', 'search', 'explore', 'create'].includes(gid)) continue;
        if (seen.has(gid)) continue;
        seen.add(gid);

        // Walk up to find the card container (up to 8 levels)
        let card = a;
        for (let i = 0; i < 8; i++) {
            if (!card.parentElement) break;
            card = card.parentElement;
            if (card.children.length > 2) break;
        }

        const text = card.innerText || '';
        const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);

        let memberText = '';
        let privacyText = 'public';
        let postFreq = '';
        let descLines = [];

        for (const line of lines) {
            // FB often crams multiple facts on one line, split by ' · '
            const segments = line.split(/\\s*·\\s*/).map(s => s.trim()).filter(Boolean);
            let hadSignal = false;
            for (const seg of segments) {
                const s = seg.toLowerCase();
                if (/\\d.*member/.test(s)) { memberText = seg; hadSignal = true; }
                else if (s === 'private group' || s === 'private') { privacyText = 'private'; hadSignal = true; }
                else if (s === 'public group' || s === 'public') { privacyText = 'public'; hadSignal = true; }
                else if (/\\d.*post/.test(s) || /post.*(day|week|month)/.test(s)) { postFreq = seg; hadSignal = true; }
            }
            if (!hadSignal && line.length > 20) descLines.push(line);
        }

        const cleanHref = href.split('?')[0];
        const fullUrl = cleanHref.startsWith('http')
            ? cleanHref
            : 'https://www.facebook.com' + cleanHref;
        cards.push({
            url: fullUrl,
            name: lines[0] || gid,
            privacy: privacyText,
            member_text: memberText,
            post_frequency: postFreq,
            description: descLines.slice(0, 3).join(' '),
        });
    }
    return cards.slice(0, 25);
}
"""

FIND_JOIN_BUTTON_JS = """
() => {
    const candidates = Array.from(
        document.querySelectorAll('[role="button"], button')
    );
    for (const btn of candidates) {
        const label = (
            btn.getAttribute('aria-label') ||
            btn.innerText ||
            ''
        ).toLowerCase().trim();
        if (label === 'join group' || label === 'join' ||
            label === 'request to join' || label === 'request') {
            btn.click();
            return 'clicked:' + label;
        }
    }
    for (const btn of candidates) {
        const label = (btn.getAttribute('aria-label') || btn.innerText || '').toLowerCase();
        if (label.includes('joined') || label.includes('member')) return 'already_joined';
        if (label.includes('pending') || label.includes('requested')) return 'already_pending';
    }
    return 'not_found';
}
"""


def search_groups(page, query: str) -> list[dict]:
    """Run a FB group search for `query` and return raw card dicts.

    Raises whatever Playwright raises; caller decides what to log.
    """
    search_url = f"https://www.facebook.com/search/groups/?q={query.replace(' ', '%20')}"
    page.goto(search_url, wait_until="domcontentloaded")
    time.sleep(4)

    # Scroll to load more results
    for _ in range(2):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(2)

    return page.evaluate(EXTRACT_GROUP_CARDS_JS)


def pace_between_queries() -> None:
    """Random 3–6s delay between FB searches to look human."""
    time.sleep(random.uniform(3, 6))


def try_join(page, url: str) -> str:
    """Navigate to a group and click the join button. Returns status string:
    'clicked:<label>' | 'already_joined' | 'already_pending' | 'not_found'.
    """
    page.goto(url, wait_until="domcontentloaded")
    time.sleep(4)
    return page.evaluate(FIND_JOIN_BUTTON_JS)


def pace_between_joins(is_last: bool = False) -> float:
    """Random 60–180s delay between join requests. Returns the chosen delay."""
    if is_last:
        return 0.0
    delay = random.uniform(60, 180)
    time.sleep(delay)
    return delay
