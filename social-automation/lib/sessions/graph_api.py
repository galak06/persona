"""Facebook / Instagram Graph API URL composition + token resolution.

Single source of truth for:
    - The Graph API version (`v23.0`) — was defined separately in
      `recipe-publisher/publishers/instagram.py:33`,
      `recipe-publisher/publishers/facebook.py:35`, and (stale at v19)
      `scripts/content_pipeline.py:230`
    - The token-fallback contract — was duplicated 7 times in
      `recipe-publisher/publishers/instagram.py`
"""

from __future__ import annotations

import os

from lib.errors.configuration import ConfigurationError

GRAPH_VERSION = "v23.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"


def graph_url(path: str) -> str:
    """Compose a Graph API URL for the configured version.

    Args:
        path: Resource path, with or without leading slash.

    Returns:
        Full URL: `https://graph.facebook.com/v23.0/<path>`.
    """
    return f"{GRAPH_BASE}/{path.lstrip('/')}"


def read_fb_token() -> str:
    """Return the Facebook Page access token from `FB_PAGE_TOKEN`.

    Raises:
        ConfigurationError: If the env var is unset or empty.
    """
    token = os.environ.get("FB_PAGE_TOKEN", "").strip()
    if not token:
        raise ConfigurationError("FB_PAGE_TOKEN env var is required but unset/empty")
    return token


def read_ig_token() -> str:
    """Return the Instagram Graph API token, preferring `FB_PAGE_TOKEN`.

    Falls back to `IG_GRAPH_ACCESS_TOKEN` for legacy compatibility.
    The Graph API treats Instagram Business accounts as Page-linked, so
    the Page token works for IG operations.

    Raises:
        ConfigurationError: If neither env var is set.
    """
    token = (
        os.environ.get("FB_PAGE_TOKEN", "").strip()
        or os.environ.get("IG_GRAPH_ACCESS_TOKEN", "").strip()
    )
    if not token:
        raise ConfigurationError(
            "FB_PAGE_TOKEN (preferred) or IG_GRAPH_ACCESS_TOKEN env var is required"
        )
    return token
