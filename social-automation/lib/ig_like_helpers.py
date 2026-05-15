"""JS payloads + parsers shared by scripts/ig_like.py.

Extracted from scripts/ig_scan.py during the 2026-05-15 split so the standalone
liker stays under the 200-line cap. Kept as a single tiny module since the
overlap with future ig_* scripts is unclear; not a stable public API.
"""

from __future__ import annotations

import re

# Competitor + own-account guards (carried over from ig_scan.py)
COMPETITOR_ACCOUNTS: set[str] = {
    "tractive", "tractivepets", "ficollar", "fidogs",
    "whistlepet", "whistle", "linkakc",
}
OWN_ACCOUNT: str = "dogfoodandfun"

EXTRACT_HASHTAG_POSTS_JS: str = r"""
() => {
    const links = Array.from(document.querySelectorAll('a[href*="/p/"]'));
    const posts = [];
    const seen = new Set();
    for (const a of links) {
        const href = a.getAttribute('href') || '';
        const match = href.match(/\/p\/([^\/]+)/);
        if (!match) continue;
        const postId = match[1];
        if (seen.has(postId)) continue;
        seen.add(postId);
        posts.push({url: 'https://www.instagram.com' + href, post_id: postId});
    }
    return posts.slice(0, 15);
}
"""

EXTRACT_POST_DETAILS_JS: str = r"""
() => {
    const result = {caption: '', like_text: '', comment_text: '', author: ''};
    const h1 = document.querySelector('h1');
    if (h1) result.caption = h1.innerText || '';
    if (!result.caption) {
        const spans = document.querySelectorAll('span[dir="auto"]');
        for (const span of spans) {
            const t = span.innerText || '';
            if (t.length > 30) { result.caption = t; break; }
        }
    }
    const authorLink = document.querySelector('header a[href]:not([href="/"])');
    if (authorLink) {
        const href = authorLink.getAttribute('href') || '';
        result.author = href.replace(/\//g, '').trim();
    }
    const allSpans = document.querySelectorAll('span');
    for (const s of allSpans) {
        const t = s.innerText || '';
        if (t.match(/\d.*like/i) || t.match(/like.*\d/i)) { result.like_text = t; break; }
    }
    for (const s of allSpans) {
        const t = s.innerText || '';
        if (t.match(/view.*\d+.*comment/i) || t.match(/\d+.*comment/i)) {
            result.comment_text = t; break;
        }
    }
    return result;
}
"""

CLICK_LIKE_JS: str = r"""
() => {
    const svgs = document.querySelectorAll('svg[aria-label="Like"]');
    for (const svg of svgs) {
        const btn = svg.closest('[role="button"]') || svg.closest('button') || svg.parentElement;
        if (btn) { btn.click(); return 'liked'; }
    }
    const btns = document.querySelectorAll('[aria-label="Like"][role="button"], button[aria-label="Like"]');
    if (btns.length > 0) { btns[0].click(); return 'liked'; }
    const unlikeSvgs = document.querySelectorAll('svg[aria-label="Unlike"]');
    if (unlikeSvgs.length > 0) return 'already_liked';
    return 'not_found';
}
"""

OVERLAY_SELECTORS: list[str] = [
    "button:has-text('Not Now')",
    "button:has-text('Cancel')",
    "button:has-text('Decline')",
    "button:has-text('Accept')",
    "[aria-label='Close']",
]


def parse_like_count(text: str) -> int:
    """Parse '1,234 likes' / '12.5K likes' into an int."""
    if not text:
        return 0
    t = text.lower().replace(",", "")
    m = re.search(r"(\d+\.?\d*)\s*k", t)
    if m:
        return int(float(m.group(1)) * 1000)
    m = re.search(r"(\d+\.?\d*)\s*m", t)
    if m:
        return int(float(m.group(1)) * 1_000_000)
    m = re.search(r"(\d+)", t)
    return int(m.group(1)) if m else 0


def ig_score(base_score: float, like_count: int) -> float:
    """IG-specific adjustments: penalize viral posts, reward small-engagement ones."""
    s = base_score
    if like_count < 500:
        s += 0.15
    if like_count > 5000:
        s -= 0.20
    return round(s, 2)


def should_scan_today(freq: str, weekday: int, ordinal: int) -> bool:
    """Whether a CSV row with `scan_frequency=<freq>` is in scope today."""
    if freq == "daily":
        return True
    if freq == "every_2_days":
        return ordinal % 2 == 0
    if freq == "weekly":
        return weekday == 0
    return False
