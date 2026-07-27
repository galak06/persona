// DOM helpers used by delete_duplicate_comments.py.
// Strategy:
//   - Find leaf spans whose textContent contains the target comment text.
//   - Walk up to the comment-row container (the one whose innerText starts with
//     the page handle and that contains a "Reply" button — signals it is a real
//     comment row, not a parent post body).
//   - On delete: click the `Comment Options` svg/button inside the row, then
//     click 'Delete' in the resulting menu, then confirm in the dialog.

const NORM = (s) => (s || '').replace(/\s+/g, ' ').trim();

// IG: page handle that posted the duplicates. Used to confirm row ownership.
const IG_HANDLE_PREFIX = 'persona';

// Find every comment row (deduped) that contains the target text.
function findIgRows(targetText) {
    const target = NORM(targetText).slice(0, 80);
    const leaves = Array.from(document.querySelectorAll('span'))
        .filter(el => el.children.length === 0 && NORM(el.textContent).includes(target));
    const rows = new Set();
    for (const leaf of leaves) {
        let p = leaf;
        for (let i = 0; i < 12 && p; i++) {
            const txt = NORM(p.innerText);
            const startsWithHandle = txt.toLowerCase().startsWith(IG_HANDLE_PREFIX);
            const hasReply = Array.from(p.querySelectorAll('div, span'))
                .some(c => NORM(c.textContent) === 'Reply' && c.children.length === 0);
            if (startsWithHandle && hasReply && txt.length < 800) {
                rows.add(p);
                break;
            }
            p = p.parentElement;
        }
    }
    return Array.from(rows);
}

// Aggressively expand IG comments: scroll page, click any "View more / N more
// comments" links, scroll the comment pane (a scrollable ancestor of any
// comment row), repeat until rows-with-our-target stops growing for 2 rounds.
window.loadAllIgComments = async (targetText, maxRounds = 20) => {
    const target = NORM(targetText).slice(0, 80);

    const findScrollables = () => {
        // Any commentish ancestor that overflows
        const cands = new Set();
        const seeds = Array.from(document.querySelectorAll('span'))
            .filter(s => s.children.length === 0 && NORM(s.textContent).toLowerCase().startsWith(IG_HANDLE_PREFIX));
        for (const s of seeds.slice(0, 30)) {
            let p = s;
            for (let i = 0; i < 12 && p; i++) {
                const cs = getComputedStyle(p);
                if ((cs.overflowY === 'auto' || cs.overflowY === 'scroll') && p.scrollHeight > p.clientHeight + 50) {
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
        // 1. scroll the page
        window.scrollTo(0, document.body.scrollHeight);
        await new Promise(rs => setTimeout(rs, 700));
        // 2. scroll any scrollable comment pane to bottom
        for (const sc of findScrollables()) {
            sc.scrollTop = sc.scrollHeight;
        }
        await new Promise(rs => setTimeout(rs, 900));
        // 3. click any expand link
        const buttons = Array.from(document.querySelectorAll('button, [role="button"], div[role="button"], span[role="button"]'))
            .filter(el => {
                const t = NORM(el.textContent).toLowerCase();
                return /view (more|all)? ?(comments|replies)/.test(t)
                    || /load more comments/.test(t)
                    || /^view \d+ (more )?(comments|replies)/.test(t);
            });
        let clicked = 0;
        for (const b of buttons) {
            try { b.click(); clicked++; } catch (e) {}
            if (clicked > 8) break;
        }
        await new Promise(rs => setTimeout(rs, 1500));
        const count = findIgRows(target).length;
        if (clicked === 0 && count === prevCount) {
            stable++;
            if (stable >= 2) break;
        } else {
            stable = 0;
        }
        prevCount = count;
    }
    return findIgRows(target).length;
};

window.findIg = (targetText) => {
    const rows = findIgRows(targetText);
    return {
        count: rows.length,
        matches: rows.slice(0, 20).map(r => ({ excerpt: NORM(r.innerText).slice(0, 120) })),
    };
};

window.igOpenMenuFirst = async (targetText) => {
    const rows = findIgRows(targetText);
    if (rows.length === 0) return 'no_match';
    rows.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
    const row = rows[0];  // topmost (oldest) — keep the newest at the bottom
    row.scrollIntoView({ block: 'center' });
    await new Promise(r => setTimeout(r, 400));
    // Hover to trigger lazy-rendered action buttons (Reply / Comment Options svg)
    row.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
    row.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
    row.dispatchEvent(new MouseEvent('mousemove', { bubbles: true }));
    await new Promise(r => setTimeout(r, 1000));
    // Now look for the Comment Options svg — first within row, then siblings/parents
    let svg = row.querySelector('svg[aria-label="Comment Options"]');
    if (!svg) {
        let p = row.parentElement;
        for (let i = 0; i < 4 && !svg && p; i++) {
            svg = p.querySelector('svg[aria-label="Comment Options"]');
            if (!svg) p = p.parentElement;
        }
    }
    if (!svg) {
        // Fallback by text match
        const all = row.querySelectorAll('div, span, button, [role="button"]');
        for (const el of all) {
            if (NORM(el.textContent).toLowerCase() === 'comment options') {
                el.click();
                return 'menu_clicked';
            }
        }
        return 'menu_not_found';
    }
    const trigger = svg.closest('[role="button"], button, div[tabindex]') || svg.parentElement;
    trigger.click();
    return 'menu_clicked';
};

// FB: handle prefix appears in 'Comment by NAME' aria-label. We match by
// substring on the comment article's innerText instead, since the page may
// post as a Page identity and the visible name varies.
function findFbRows(targetText) {
    const target = NORM(targetText).slice(0, 80);
    const articles = Array.from(document.querySelectorAll('div[role="article"]'));
    return articles.filter(art => {
        const aria = (art.getAttribute('aria-label') || '').toLowerCase();
        if (!aria.startsWith('comment by')) return false;
        return NORM(art.innerText).includes(target);
    });
}

window.loadAllFbComments = async (maxRounds = 15) => {
    let stableRounds = 0;
    let prevCount = 0;
    for (let r = 0; r < maxRounds; r++) {
        // 1. scroll to bottom to bring loaders into view
        window.scrollTo(0, document.body.scrollHeight);
        await new Promise(r => setTimeout(r, 1200));
        // 2. find + click "View more comments" / "View N replies"
        const cands = Array.from(document.querySelectorAll('div[role="button"], span[role="button"], button, a'))
            .filter(el => {
                const t = NORM(el.textContent).toLowerCase();
                return /view (more|all)? ?(comments|replies)/.test(t)
                    || /load more/.test(t)
                    || /^view \d+/.test(t);
            });
        let clicked = 0;
        for (const c of cands) {
            try { c.click(); clicked++; } catch (e) {}
            if (clicked > 6) break;
        }
        await new Promise(r => setTimeout(r, 1800));
        const count = document.querySelectorAll('div[role="article"]').length;
        if (clicked === 0 && count === prevCount) {
            stableRounds++;
            if (stableRounds >= 2) break;  // two stable rounds → done
        } else {
            stableRounds = 0;
        }
        prevCount = count;
    }
    return document.querySelectorAll('div[role="article"]').length;
};

window.findFb = (targetText) => {
    const rows = findFbRows(targetText);
    return {
        count: rows.length,
        matches: rows.slice(0, 20).map(r => ({
            aria: r.getAttribute('aria-label') || '',
            excerpt: NORM(r.innerText).slice(0, 120),
        })),
    };
};

window.fbOpenMenuFirst = (targetText) => {
    const rows = findFbRows(targetText);
    if (rows.length === 0) return 'no_match';
    rows.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
    const row = rows[0];
    row.scrollIntoView({ block: 'center' });
    row.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
    row.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
    const btns = Array.from(row.querySelectorAll('[role="button"][aria-label]'));
    for (const b of btns) {
        const lbl = (b.getAttribute('aria-label') || '').toLowerCase();
        if (lbl.includes('action') || lbl.includes('more') || lbl.includes('options')) {
            b.click();
            return 'menu_clicked';
        }
    }
    return 'menu_not_found';
};

window.clickDeleteOption = () => {
    const items = Array.from(document.querySelectorAll(
        '[role="menuitem"], [role="menu"] [role="button"], [role="dialog"] [role="button"], [role="dialog"] button, div[role="menu"] div'
    ));
    for (const it of items) {
        const t = NORM(it.textContent).toLowerCase();
        if (t === 'delete' || t === 'remove' || t === 'delete comment') {
            it.click();
            return 'delete_clicked';
        }
    }
    return 'delete_option_not_found';
};

window.confirmDeleteDialog = () => {
    const btns = Array.from(document.querySelectorAll('[role="dialog"] button, [role="dialog"] [role="button"]'));
    for (const b of btns) {
        const t = NORM(b.textContent).toLowerCase();
        if (t === 'delete' || t === 'confirm' || t === 'yes' || t === 'remove') {
            b.click();
            return 'confirmed';
        }
    }
    return 'no_confirm_dialog';  // some IG flows delete without confirmation
};
