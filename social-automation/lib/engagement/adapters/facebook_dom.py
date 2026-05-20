"""Facebook DOM constants — JS payloads + CSS selectors used by FacebookGroupAdapter.

These are extracted verbatim from scripts/fb_scan.py so the adapter module can
stay under 300 lines. No behavior change: same selectors, same walker logic,
same fallback chain.
"""
from __future__ import annotations

# Post extraction JS
# Uses two proven selectors from open-source scrapers:
#   1. data-ad-rendering-role="story_message" (facebook-group-scraper)
#   2. [role="article"] / div[dir="auto"] fallback (Facebook-Scraper)
# Lifted verbatim from scripts/fb_scan.py lines 145-262.

EXTRACT_POSTS_JS = """
() => {
    const posts = [];
    const seen = new Set();

    // Helper: extract a post URL from a container
    function findPostUrl(container) {
        const links = container.querySelectorAll('a[href]');
        // Direct post links
        for (const a of links) {
            const href = a.href || '';
            if (href.includes('/posts/') || href.includes('/permalink/')) {
                return href.split('?')[0];
            }
        }
        // Group post pattern: /groups/ID/NUMBER
        for (const a of links) {
            const href = a.href || '';
            if (href.match(/\\/groups\\/[^/]+\\/\\d{5,}/)) {
                return href.split('?')[0];
            }
        }
        // Timestamp links (e.g. "2h", "Apr 14") — these link to the post
        for (const a of links) {
            const text = (a.innerText || '').trim();
            const href = a.href || '';
            if (href.includes('/groups/') && href.match(/\\/\\d{5,}/) &&
                (text.match(/^\\d+[hmd]$/i) ||
                 text.match(/^(Yesterday|Just now|\\d+ min)/i) ||
                 text.match(/^[A-Z][a-z]{2,8} \\d/))) {
                return href.split('?')[0];
            }
        }
        return '';
    }

    // Helper: get comment count
    function getCommentCount(container) {
        const all = container.querySelectorAll('span, [aria-label]');
        for (const el of all) {
            const t = el.innerText || el.getAttribute('aria-label') || '';
            const m = t.match(/(\\d+)\\s*comment/i);
            if (m) return parseInt(m[1], 10);
        }
        return 0;
    }

    // Strategy: walk the feed looking for story_message divs
    // These ONLY appear on top-level posts, never on comments
    const storyMsgs = document.querySelectorAll(
        'div[data-ad-rendering-role="story_message"]'
    );

    storyMsgs.forEach(msgEl => {
        try {
            const text = msgEl.innerText?.trim();
            if (!text || text.length < 15) return;

            const key = text.substring(0, 100);
            if (seen.has(key)) return;
            seen.add(key);

            // Walk up to the post container to find URL + metadata
            // Go up several levels to find the post wrapper
            let container = msgEl;
            for (let i = 0; i < 15; i++) {
                container = container.parentElement;
                if (!container) break;
                // Stop at the outermost article
                if (container.getAttribute('role') === 'article') break;
                // Or at a pagelet boundary
                if (container.dataset?.pagelet) break;
            }
            if (!container) container = msgEl.parentElement;

            posts.push({
                text: text.substring(0, 800),
                url: findPostUrl(container),
                comment_count: getCommentCount(container),
                timestamp: '',
            });
        } catch(e) {}
    });

    // Fallback: if story_message found nothing, try top-level articles
    if (posts.length === 0) {
        const articles = document.querySelectorAll('[role="feed"] > div [role="article"]');
        articles.forEach(article => {
            try {
                // Only top-level: skip if this article is inside another article
                const parent = article.parentElement?.closest('[role="article"]');
                if (parent) return;

                const textEls = article.querySelectorAll('[dir="auto"]');
                let text = '';
                for (const el of textEls) {
                    const t = el.innerText?.trim();
                    if (t && t.length > 15) { text = t; break; }
                }
                if (!text) return;

                const key = text.substring(0, 100);
                if (seen.has(key)) return;
                seen.add(key);

                posts.push({
                    text: text.substring(0, 800),
                    url: findPostUrl(article),
                    comment_count: getCommentCount(article),
                    timestamp: '',
                });
            } catch(e) {}
        });
    }

    return posts.slice(0, 20);
}
"""

# Diagnostic counters — used to log selector hit counts before the main extraction.
STORY_MESSAGE_COUNT_JS = (
    "() => document.querySelectorAll('[data-ad-rendering-role=\"story_message\"]').length"
)
ARTICLE_COUNT_JS = '() => document.querySelectorAll(\'[role="article"]\').length'


# Overlay-dismissal selectors — group welcome popups, login prompts, etc.
# Lifted verbatim from scripts/fb_scan.py lines 268-280.
OVERLAY_DISMISS_SELECTORS: tuple[str, ...] = (
    # Close button (X) on dialogs
    "[aria-label='Close']",
    "[aria-label='close']",
    # Generic dialog close buttons
    "div[role='dialog'] div[aria-label='Close']",
    "div[role='dialog'] [role='button']:has-text('Not Now')",
    "div[role='dialog'] [role='button']:has-text('OK')",
    "div[role='dialog'] [role='button']:has-text('Got it')",
    "div[role='dialog'] [role='button']:has-text('Skip')",
    # "Save login info" dialog
    "div[role='button']:has-text('Not Now')",
)


# User-agent string for the Playwright context — pinned to match fb_scan.py
# so Facebook sees the same browser fingerprint as the existing scanner.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
