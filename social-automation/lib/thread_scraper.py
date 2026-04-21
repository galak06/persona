"""Playwright helpers for Facebook comment threads.

Finds our previously-posted comment on a FB post by matching its first ~60
chars, scrapes any replies that landed underneath it, and clicks the Reply
affordance under our comment to post a threaded response.

DOM selectors are best-effort — FB rotates them. The JS evaluators below fall
back across several patterns. If a call returns "not_found", open devtools on
a known post and update the selectors; every query is isolated so changes are
contained.

Only the scraping + threaded-reply bits live here; the session/browser bootstrap
is reused from `scripts/comment_poster.py` via its chromium context pattern.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# How much of our comment's text to match against the post DOM.
# FB truncates long comments at ~50 chars with "… See more". We expand those
# first, then still keep the match prefix short (25) for robustness — our
# first 25 chars are almost always unique within a given post.
_MATCH_PREFIX_LEN = 25


@dataclass
class ScrapedReply:
    author: str
    text: str
    # DOM-path-ish fingerprint used to dedup replies across runs.
    # Falls back to (author + text[:80]) when FB doesn't expose a stable id.
    fingerprint: str


def _expand_see_more(page) -> int:
    """Click every "See more" / "See X more replies" link so full text + replies load."""
    return page.evaluate(
        """() => {
        let n = 0;
        const clickables = Array.from(document.querySelectorAll('[role="button"], a, span'));
        for (const el of clickables) {
            const t = (el.textContent || '').trim().toLowerCase();
            if (
                t === 'see more' ||
                t.startsWith('see more ') ||
                t.startsWith('view more ') ||
                t.startsWith('view all') ||
                t.includes('previous replies') ||
                t.includes('more replies') ||
                t.includes('more comments')
            ) {
                try { el.click(); n++; } catch (e) {}
            }
        }
        return n;
    }"""
    )


def _needle_in_dom(page, needle: str) -> bool:
    return page.evaluate(
        """(needle) => {
        const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
        const articles = document.querySelectorAll('[role="article"]');
        for (const el of articles) {
            if (norm(el.textContent).indexOf(needle) !== -1) return true;
        }
        return false;
    }""",
        needle,
    )


def find_replies_to_my_comment(page, my_comment_text: str) -> list[ScrapedReply]:
    """Open the already-loaded post page, locate our comment, return replies under it."""
    prefix = my_comment_text[:_MATCH_PREFIX_LEN]
    needle = prefix.strip().lower()
    # FB sorts comments by "Most relevant" and paginates. Older, low-engagement
    # comments sit below the initial batch, so expand + scroll in a loop until
    # our comment surfaces or we give up.
    for _ in range(8):
        _expand_see_more(page)
        time.sleep(1.2)
        if _needle_in_dom(page, needle):
            break
        page.evaluate("window.scrollBy(0, window.innerHeight)")
        time.sleep(0.8)
    result = page.evaluate(
        """(prefix) => {
        // Walk every candidate comment element, score by text-prefix match.
        // Narrow to actual comment containers — role=article is the reliable
        // anchor on FB. The other selectors pollute with unrelated divs.
        const candidates = Array.from(document.querySelectorAll('[role="article"]'));
        const candidateCount = candidates.length;
        const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
        const needle = norm(prefix).toLowerCase();
        // Collect every candidate whose text contains the needle anywhere,
        // then pick the one with the *smallest* textContent — that's the
        // innermost comment container (not the post wrapper that nests it).
        const hits = [];
        for (const el of candidates) {
            const t = norm(el.textContent).toLowerCase();
            if (t.indexOf(needle) !== -1) hits.push({el, len: t.length});
        }
        hits.sort((a, b) => a.len - b.len);
        let mine = hits.length ? hits[0].el : null;
        // On miss, capture a sample of candidate texts so we can see what's
        // actually on the page (author prefixes, truncation markers, etc.)
        let sample = null;
        if (!mine) {
            sample = candidates.slice(0, 15).map(el => {
                return norm(el.textContent).slice(0, 80);
            });
        }
        if (!mine) return {status: 'my_comment_not_found', candidates: candidateCount, sample};

        // Replies usually live in a sibling container right after my comment,
        // often inside an element labeled "Reply" / "replies".
        // Strategy: pick the closest ancestor that also contains other articles,
        // then collect articles that come AFTER mine in document order and are
        // indented (left offset > mine's) — those are its replies.
        const myRect = mine.getBoundingClientRect();
        const allArticles = Array.from(document.querySelectorAll('[role="article"]'));
        const idx = allArticles.indexOf(mine);
        const out = [];
        for (let i = idx + 1; i < allArticles.length; i++) {
            const el = allArticles[i];
            const r = el.getBoundingClientRect();
            // Stop scanning once we hit a comment that starts at or left of
            // our comment's indent — that's the next top-level comment.
            if (r.left <= myRect.left + 4) break;
            const text = norm(el.textContent);
            if (!text) continue;
            // Skip our own comment / duplicates
            if (text.toLowerCase().startsWith(needle)) continue;
            // Extract author: first <a> tag inside the reply usually links to profile
            const anchor = el.querySelector('a[role="link"][tabindex="0"]');
            const author = norm(anchor ? anchor.textContent : '') || 'unknown';
            // Strip the author name out of the text to get the reply body
            let body = text;
            if (author && body.startsWith(author)) body = body.slice(author.length).trim();
            out.push({
                author,
                text: body.slice(0, 800),
                fingerprint: (author + '|' + body.slice(0, 80)),
            });
        }
        return {status: 'ok', mine_index: idx, replies: out};
    }""",
        prefix,
    )
    if result.get("status") != "ok":
        print(
            f"find_replies: {result.get('status')} "
            f"(prefix={prefix!r}, dom_candidates={result.get('candidates')})",
            flush=True,
        )
        sample = result.get("sample") or []
        for i, s in enumerate(sample[:10]):
            print(f"  candidate[{i}] = {s!r}", flush=True)
        return []
    return [ScrapedReply(**r) for r in result.get("replies", [])]


def post_threaded_reply_fb(page, my_comment_text: str, reply_body: str) -> bool:
    """Click the Reply button under our comment and submit `reply_body`."""
    prefix = my_comment_text[:_MATCH_PREFIX_LEN]
    clicked = page.evaluate(
        """(prefix) => {
        const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
        const needle = norm(prefix).toLowerCase();
        const articles = Array.from(document.querySelectorAll('[role="article"]'));
        let mine = null;
        for (const el of articles) {
            const t = norm(el.textContent).toLowerCase();
            if (t.startsWith(needle) || t.indexOf(needle) < 120) { mine = el; break; }
        }
        if (!mine) return 'my_comment_not_found';
        // Find Reply button scoped to my comment — usually a role=button whose
        // textContent (or aria-label) is exactly "Reply".
        const buttons = Array.from(mine.querySelectorAll('[role="button"], a'));
        const reply = buttons.find(b => {
            const l = (b.getAttribute('aria-label') || '').trim().toLowerCase();
            const t = norm(b.textContent).toLowerCase();
            return l === 'reply' || t === 'reply';
        });
        if (!reply) return 'reply_button_not_found';
        reply.click();
        return 'clicked';
    }""",
        prefix,
    )
    logger.info("post_threaded_reply: reply-button click=%s", clicked)
    if clicked != "clicked":
        return False
    time.sleep(1.5)

    # After clicking Reply, a reply composer opens nearby. Reuse the same
    # contenteditable-finder pattern as post_comment_fb.
    found = page.evaluate(
        """() => {
        const sels = [
            '[contenteditable="true"][data-lexical-editor="true"]',
            '[contenteditable="true"][aria-label*="reply" i]',
            '[contenteditable="true"][aria-label*="Write a reply" i]',
            '[contenteditable="true"][role="textbox"]',
        ];
        for (const s of sels) {
            const box = document.querySelector(s);
            if (box) { box.focus(); box.click(); return 'found:' + s; }
        }
        return 'not_found';
    }"""
    )
    if not found.startswith("found"):
        logger.warning("reply composer not found after clicking Reply")
        return False

    time.sleep(0.8)
    page.keyboard.type(reply_body, delay=30)
    time.sleep(1.5)

    submitted = page.evaluate(
        """() => {
        const btns = Array.from(document.querySelectorAll('[role="button"]'));
        const btn = btns.find(b => {
            const l = (b.getAttribute('aria-label') || '').toLowerCase();
            return l === 'reply' || l === 'post' || l === 'comment';
        });
        if (btn) { btn.click(); return 'clicked'; }
        return 'not_found';
    }"""
    )
    if submitted != "clicked":
        page.keyboard.press("Enter")
    logger.info("post_threaded_reply: submit=%s", submitted)
    time.sleep(3)
    return True
