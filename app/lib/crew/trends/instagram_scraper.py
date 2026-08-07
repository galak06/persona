"""Instagram trends-feed scraper -- a new trend-signal source for the
market-trend scout (`lib.crew.trends.agent`).

Reuses `lib.sessions.browser.BrowserSession`/`BrowserSessionConfig`, the
existing, proven, cron-safe Playwright wrapper already used by
`fb_session`/`ig_session`/`tiktok_session` -- not a new bespoke Playwright
setup. `instagram.com/popular/instagram-trends/` is public and needs no
login, so this points `storage_state_path` at a dedicated scratch file
rather than reusing `ig_session()`'s login-required cookie path, and forces
`headless=True` unconditionally (no authenticated account to protect here,
unlike the FB/IG engagement scanners which default headless off to dodge
bot-detection flags on a real login).

Extracts raw visible page text (`page.inner_text("body")`), not structured
DOM/CSS-selector parsing like `lib.engagement.adapters.instagram_dom`'s
`EXTRACT_HASHTAG_POSTS_JS`. Instagram's DOM/class names are exactly the kind
of fragile, frequently-changing target that makes bespoke selectors
high-maintenance; since the consumer here is an LLM
(`lib.crew.trends.agent`'s scout), not a `post_id`/`like_count` extraction
pipeline, raw cleaned page text is sufficient and dramatically
lower-maintenance.

Never raises -- every call site (page navigation, JS evaluation, text
extraction) is wrapped in try/except so a scrape failure just means the
Trends stage degrades gracefully (GSC + Serper signals still run normally).
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from lib.observability import get_logger
from lib.sessions.browser import BrowserSession, BrowserSessionConfig

logger = get_logger(__name__)

INSTAGRAM_TRENDS_URL = "https://www.instagram.com/popular/instagram-trends/"

_RENDER_WAIT_MS = 4_000
_SCROLL_PASSES = 2
_SCROLL_WAIT_S = 2

_INTRA_LINE_WHITESPACE_RE = re.compile(r"[ \t]{2,}")


def _clean_text(text: str) -> str:
    """Strip blank lines and collapse repeated intra-line whitespace -- the
    LLM consumer only needs readable prose, not the page's original layout."""
    lines = [_INTRA_LINE_WHITESPACE_RE.sub(" ", line.strip()) for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def fetch_instagram_trends_text(
    brand_dir: Path,
    *,
    url: str = INSTAGRAM_TRENDS_URL,
    max_chars: int = 4000,
) -> str | None:
    """Scrape the public Instagram trends page and return cleaned, truncated
    visible page text -- or `None` on any failure (never raises).

    Navigates, waits for the JS-rendered feed to settle, scrolls a couple of
    passes for better coverage (mirrors
    `lib.engagement.adapters.instagram.InstagramHashtagAdapter.iterate_posts`'s
    scroll pattern), then extracts `page.inner_text("body")`.
    """
    storage_state_path = brand_dir / "state" / "instagram_trends_scrape_session.json"
    config = BrowserSessionConfig(storage_state_path=storage_state_path, headless=True)

    try:
        with BrowserSession(config) as page:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            except Exception as exc:
                # Mirrors `InstagramHashtagAdapter.iterate_posts`'s fallback --
                # IG pages sometimes stall on DOMContentLoaded due to redirect
                # chains; fall back to waiting for the network to settle. Log
                # first: the fallback below typically still succeeds even when
                # `goto` failed for an unrelated reason (login wall, CAPTCHA,
                # "page isn't available" interstitial), so without this line
                # a bad navigation can silently produce a "successful" scrape
                # of the wrong page.
                logger.warning("crew_trends_instagram_goto_stalled", error=str(exc))
                page.wait_for_load_state("networkidle", timeout=15_000)
            page.wait_for_timeout(_RENDER_WAIT_MS)
            for _ in range(_SCROLL_PASSES):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(_SCROLL_WAIT_S)
            raw_text = page.inner_text("body")
    except Exception as exc:
        logger.warning("crew_trends_instagram_scrape_failed", error=str(exc))
        return None

    cleaned = _clean_text(raw_text)
    if not cleaned:
        logger.warning("crew_trends_instagram_scrape_empty")
        return None
    return cleaned[:max_chars]
