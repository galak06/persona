"""Facebook DOM payloads for the own-comment read (see `own_comment.py`).

A sibling of `facebook_dom.py` rather than more constants inside it: that file
is already 217 lines of JS blobs, and these two payloads push it past the
300-line cap. Same convention — JS-as-string-constant, one `page.evaluate`
payload per name.
"""

from __future__ import annotations

# --- JS: is OUR comment already on this post? ---------------------------------
#
# Ported from scripts/_dedup_dom.js (`loadAllFbComments` / `findFbRows`), which
# already cleaned up a live round of duplicate comments — the expand-until-
# stable loop below is that loop, unchanged in behaviour.
#
# One deliberate change: the original required the comment article's aria-label
# to start with "comment by". FB omits or rewords that label when the comment
# was posted under a Page identity (which is exactly how we comment), so the
# requirement can miss OUR OWN comment — a false negative, the one error
# direction that reproduces the duplicate-comment bug. Dropped; we match on
# text alone. See lib/engagement/adapters/own_comment.py for the rationale.
#
# Nesting note: a story article contains its comment articles, so a naive
# filter counts one comment twice (once in the comment, once in the enclosing
# story). `fbRows` keeps only the innermost matching articles.

_FB_ROW_HELPERS_JS: str = """
    const NORM = (s) => (s || '').replace(/\\s+/g, ' ').trim();
    const normTarget = (t) => NORM(t).toLowerCase().slice(0, 80);
    const fbRows = (target) => {
        if (!target) return [];
        const hits = Array.from(document.querySelectorAll('div[role="article"]'))
            .filter(art => NORM(art.innerText).toLowerCase().includes(target));
        return hits.filter(art => !hits.some(other => other !== art && art.contains(other)));
    };
"""

# `targetText` is accepted but unused: the stability signal is the total
# article count (verbatim from _dedup_dom.js), and the uniform
# `page.evaluate(JS, drafted_text)` shape lets own_comment.py drive IG and FB
# through one code path.
LOAD_ALL_FB_COMMENTS_JS: str = """
async (targetText) => {
    const NORM = (s) => (s || '').replace(/\\s+/g, ' ').trim();
    const maxRounds = 15;
    let stableRounds = 0;
    let prevCount = 0;
    for (let r = 0; r < maxRounds; r++) {
        window.scrollTo(0, document.body.scrollHeight);
        await new Promise(rs => setTimeout(rs, 1200));
        const cands = Array.from(document.querySelectorAll(
            'div[role="button"], span[role="button"], button, a'
        )).filter(el => {
            const t = NORM(el.textContent).toLowerCase();
            return /view (more|all)? ?(comments|replies)/.test(t)
                || /load more/.test(t)
                || /^view \\d+/.test(t);
        });
        let clicked = 0;
        for (const c of cands) {
            try { c.click(); clicked++; } catch (e) {}
            if (clicked > 6) break;
        }
        await new Promise(rs => setTimeout(rs, 1800));
        const count = document.querySelectorAll('div[role="article"]').length;
        if (clicked === 0 && count === prevCount) {
            stableRounds++;
            if (stableRounds >= 2) break;
        } else {
            stableRounds = 0;
        }
        prevCount = count;
    }
    return document.querySelectorAll('div[role="article"]').length;
}
"""

FIND_OWN_COMMENT_FB_JS: str = (
    """
(targetText) => {"""
    + _FB_ROW_HELPERS_JS
    + """
    const target = normTarget(targetText);
    const rows = fbRows(target);
    if (rows.length > 0) return {count: rows.length};
    // Last resort: the text is on the page but not inside any article FB gave
    // us. Still a match — bias toward "found".
    const body = document.body ? NORM(document.body.innerText).toLowerCase() : '';
    return {count: (target && body.includes(target)) ? 1 : 0};
}
"""
)
