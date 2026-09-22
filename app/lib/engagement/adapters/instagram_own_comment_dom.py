"""Instagram DOM payloads for the own-comment read (see `own_comment.py`).

A sibling of `instagram_dom.py` rather than more constants inside it: that file
is already 180 lines of JS blobs, and these two payloads push it past the
300-line cap. Same convention — JS-as-string-constant, one `page.evaluate`
payload per name.
"""

from __future__ import annotations

# --- JS: is OUR comment already on this post? ---------------------------------
#
# Ported from scripts/_dedup_dom.js (`loadAllIgComments` / `findIgRows`), which
# already cleaned up a live round of duplicate comments — the expand-until-
# stable loop below is that loop, unchanged in behaviour.
#
# One deliberate change: the `IG_HANDLE_PREFIX` ownership test is GONE. The
# original confirmed a row belonged to us by checking that its innerText
# started with the posting account's handle. That is brand-specific (it needs
# to know who "we" are) and, worse, it is biased toward NOT finding a match —
# IG renders the author name inconsistently across viewports and locales, so a
# row that IS ours can fail the prefix test. Here we match on text alone.
# See lib/engagement/adapters/own_comment.py for why that bias is the correct
# one for this read.
#
# Both constants are `page.evaluate(JS, drafted_text)` payloads; they do their
# own normalization (whitespace-collapse, lowercase, 80-char prefix) so the
# caller passes the raw drafted comment.

_IG_ROW_HELPERS_JS: str = """
    const NORM = (s) => (s || '').replace(/\\s+/g, ' ').trim();
    const normTarget = (t) => NORM(t).toLowerCase().slice(0, 80);
    // A comment row is the nearest ancestor that owns a "Reply" affordance and
    // is still short enough not to be the whole post body.
    const hasReply = (el) => Array.from(el.querySelectorAll('div, span'))
        .some(c => c.children.length === 0 && NORM(c.textContent) === 'Reply');
    const igRows = (target) => {
        if (!target) return [];
        const leaves = Array.from(document.querySelectorAll('span'))
            .filter(el => el.children.length === 0 &&
                          NORM(el.textContent).toLowerCase().includes(target));
        const rows = new Set();
        for (const leaf of leaves) {
            let p = leaf, row = null;
            for (let i = 0; i < 12 && p; i++) {
                if (NORM(p.innerText).length < 800 && hasReply(p)) { row = p; break; }
                p = p.parentElement;
            }
            // No row container resolved (lazy-rendered Reply button) — keep the
            // leaf itself. Losing the match would read as "not commented".
            rows.add(row || leaf);
        }
        return Array.from(rows);
    };
"""

LOAD_ALL_IG_COMMENTS_JS: str = (
    """
async (targetText) => {"""
    + _IG_ROW_HELPERS_JS
    + """
    const target = normTarget(targetText);
    const maxRounds = 20;

    // The comment pane is a scrollable ancestor of a comment row. Seed the walk
    // from "Reply" leaves (brand-agnostic) rather than from our own handle.
    const findScrollables = () => {
        const cands = new Set();
        const seeds = Array.from(document.querySelectorAll('div, span'))
            .filter(el => el.children.length === 0 && NORM(el.textContent) === 'Reply');
        for (const s of seeds.slice(0, 30)) {
            let p = s;
            for (let i = 0; i < 12 && p; i++) {
                const cs = getComputedStyle(p);
                if ((cs.overflowY === 'auto' || cs.overflowY === 'scroll') &&
                    p.scrollHeight > p.clientHeight + 50) {
                    cands.add(p);
                    break;
                }
                p = p.parentElement;
            }
        }
        return Array.from(cands);
    };

    let stable = 0, prevCount = -1;
    for (let r = 0; r < maxRounds; r++) {
        window.scrollTo(0, document.body.scrollHeight);
        await new Promise(rs => setTimeout(rs, 700));
        for (const sc of findScrollables()) {
            sc.scrollTop = sc.scrollHeight;
        }
        await new Promise(rs => setTimeout(rs, 900));
        const buttons = Array.from(document.querySelectorAll(
            'button, [role="button"], div[role="button"], span[role="button"]'
        )).filter(el => {
            const t = NORM(el.textContent).toLowerCase();
            return /view (more|all)? ?(comments|replies)/.test(t)
                || /load more comments/.test(t)
                || /^view \\d+ (more )?(comments|replies)/.test(t);
        });
        let clicked = 0;
        for (const b of buttons) {
            try { b.click(); clicked++; } catch (e) {}
            if (clicked > 8) break;
        }
        await new Promise(rs => setTimeout(rs, 1500));
        const count = igRows(target).length;
        if (clicked === 0 && count === prevCount) {
            stable++;
            if (stable >= 2) break;
        } else {
            stable = 0;
        }
        prevCount = count;
    }
    return igRows(target).length;
}
"""
)

FIND_OWN_COMMENT_IG_JS: str = (
    """
(targetText) => {"""
    + _IG_ROW_HELPERS_JS
    + """
    const target = normTarget(targetText);
    const rows = igRows(target);
    if (rows.length > 0) return {count: rows.length};
    // Last resort: the text is on the page but not inside anything we could
    // resolve to a row. Still a match — bias toward "found".
    const body = document.body ? NORM(document.body.innerText).toLowerCase() : '';
    return {count: (target && body.includes(target)) ? 1 : 0};
}
"""
)
