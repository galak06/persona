"""Instagram DOM payloads + constants for InstagramHashtagAdapter.

Holds the inline JavaScript blobs run by Playwright on Instagram pages, plus
the platform-specific constants the adapter consults during pre-filtering.
Verbatim relocation of the JS payloads previously embedded in ig_scan.py and
the deleted IG-like-helpers module — these strings live here so
lib/engagement/adapters/instagram.py stays under the 300-line cap.

NOTE: this module exceeds 300 lines (multi-line JS payloads + constants).
That's intentional: it is data, not logic, and keeps instagram.py readable.
"""

from __future__ import annotations

# --- Account guards -----------------------------------------------------------

# Known competitor brand accounts — never like their posts.
COMPETITOR_ACCOUNTS: set[str] = {
    "tractive",
    "tractivepets",
    "ficollar",
    "fidogs",
    "whistlepet",
    "whistle",
    "linkakc",
}

# Our own account — skip to avoid self-engagement.
OWN_ACCOUNT: str = "dogfoodandfun"


# --- Overlay dismissers -------------------------------------------------------

OVERLAY_SELECTORS: list[str] = [
    "button:has-text('Not Now')",
    "button:has-text('Cancel')",
    "button:has-text('Decline')",
    "button:has-text('Accept')",  # cookie consent
    "[aria-label='Close']",
]


# --- JS: extract post links from a hashtag listing page -----------------------

EXTRACT_HASHTAG_POSTS_JS: str = """
() => {
    const links = Array.from(document.querySelectorAll('a[href*="/p/"]'));
    const posts = [];
    const seen = new Set();

    for (const a of links) {
        const href = a.getAttribute('href') || '';
        const match = href.match(/\\/p\\/([^\\/]+)/);
        if (!match) continue;
        const postId = match[1];
        if (seen.has(postId)) continue;
        seen.add(postId);

        posts.push({
            url: 'https://www.instagram.com' + href,
            post_id: postId,
        });
    }
    return posts.slice(0, 15);
}
"""


# --- JS: extract caption / author / like_text / comment_text from a post page -

EXTRACT_POST_DETAILS_JS: str = """
() => {
    const result = {caption: '', like_text: '', comment_text: '', author: ''};

    // Caption — multiple selector strategies
    const h1 = document.querySelector('h1');
    if (h1) result.caption = h1.innerText || '';

    if (!result.caption) {
        // Fallback: look for the main text block in the post
        const spans = document.querySelectorAll('span[dir="auto"]');
        for (const span of spans) {
            const t = span.innerText || '';
            if (t.length > 30) {
                result.caption = t;
                break;
            }
        }
    }

    // Author — strip all slashes from href e.g. /dogfoodandfun/ -> dogfoodandfun
    const authorLink = document.querySelector(
        'header a[href]:not([href="/"])'
    );
    if (authorLink) {
        const href = authorLink.getAttribute('href') || '';
        result.author = href.replace(/\\//g, '').trim();
    }
    // Fallback: try the first link with a username-like path
    if (!result.author) {
        const links = document.querySelectorAll('a[href^="/"]');
        for (const a of links) {
            const h = a.getAttribute('href') || '';
            if (h.match(/^\\/[a-zA-Z0-9_.]+\\/$/) && h !== '/') {
                result.author = h.replace(/\\//g, '').trim();
                break;
            }
        }
    }

    // Like count
    const allSpans = document.querySelectorAll('span');
    for (const s of allSpans) {
        const t = s.innerText || '';
        if (t.match(/\\d.*like/i) || t.match(/like.*\\d/i)) {
            result.like_text = t;
            break;
        }
    }

    // Comment count
    for (const s of allSpans) {
        const t = s.innerText || '';
        if (t.match(/view.*\\d+.*comment/i) || t.match(/\\d+.*comment/i)) {
            result.comment_text = t;
            break;
        }
    }

    return result;
}
"""


# --- JS: click the like button on the currently-open post page ----------------

CLICK_LIKE_JS: str = """
() => {
    // Find the like button (heart icon) — multiple strategies
    const svgs = document.querySelectorAll('svg[aria-label="Like"]');
    for (const svg of svgs) {
        const btn = svg.closest('[role="button"]') ||
                    svg.closest('button') ||
                    svg.parentElement;
        if (btn) {
            btn.click();
            return 'liked';
        }
    }

    // Fallback: aria-label on the button itself
    const btns = document.querySelectorAll(
        '[aria-label="Like"][role="button"], button[aria-label="Like"]'
    );
    if (btns.length > 0) {
        btns[0].click();
        return 'liked';
    }

    // Check if already liked
    const unlikeSvgs = document.querySelectorAll('svg[aria-label="Unlike"]');
    if (unlikeSvgs.length > 0) {
        return 'already_liked';
    }

    return 'not_found';
}
"""
