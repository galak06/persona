"""Session/network helpers — Playwright browsers, HTTP clients, Graph URLs.

Replaces:
    - 14 inline Playwright `sync_playwright().__enter__() + browser.launch()
      + new_context(storage_state, viewport, user_agent) + new_page()`
      reimplementations across `scripts/*.py`
    - 4 inline `httpx.Client(base_url, auth=(WP_USER, WP_APP_PASSWORD), ...)`
      reimplementations
    - 3 different `https://graph.facebook.com/v??.0` literals (one stale at v19)

Design goals:
    - Single source of truth for User-Agent string (was inlined ~14 times)
    - Single source of truth for Graph API version (was 3 different values)
    - Single source of truth for env-var contract (`WP_URL`/`WP_USER`/
      `WP_APP_PASSWORD`, `FB_PAGE_TOKEN` with `IG_GRAPH_ACCESS_TOKEN`
      fallback) — caller errors surface as `ConfigurationError`, not KeyError
    - Resource lifecycle as context managers — caller can't leak a browser
"""

from lib.sessions.browser import (
    USER_AGENT,
    BrowserSession,
    BrowserSessionConfig,
    fb_session,
    ig_session,
    tiktok_session,
)
from lib.sessions.graph_api import (
    GRAPH_BASE,
    GRAPH_VERSION,
    graph_url,
    read_fb_token,
    read_ig_token,
)
from lib.sessions.wp_client import wp_client

__all__ = [
    "GRAPH_BASE",
    "GRAPH_VERSION",
    "USER_AGENT",
    "BrowserSession",
    "BrowserSessionConfig",
    "fb_session",
    "graph_url",
    "ig_session",
    "read_fb_token",
    "read_ig_token",
    "tiktok_session",
    "wp_client",
]
