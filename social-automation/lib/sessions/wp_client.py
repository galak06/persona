"""WordPress REST API client — single httpx.Client factory.

Replaces 4 inline implementations across:
    - scripts/comment_poster.py:94-97
    - scripts/wp_scan.py:84-93
    - recipe-publisher/publishers/wordpress.py:41-52 (with alternate env names)
    - scripts/content_pipeline.py:147-152 (used `requests`, not `httpx`!)

Standardizes the env-var contract on `WP_URL` / `WP_USER` /
`WP_APP_PASSWORD`. The recipe-publisher's `WP_BASE_URL` /
`WP_APP_PASSWORD_USER` aliases will be removed in Stage 4 (drift fixes).

Returns an `httpx.Client` configured with:
    - `base_url` from `WP_URL` (trailing slash stripped)
    - basic auth from `WP_USER`/`WP_APP_PASSWORD`
    - 30s default timeout
    - Standard User-Agent for log attribution

Caller is responsible for closing the client (use as context manager):

    from lib.sessions import wp_client

    with wp_client() as client:
        r = client.get("/wp-json/wp/v2/posts?per_page=1")
"""

from __future__ import annotations

import os

import httpx

from lib.errors.configuration import ConfigurationError

_DEFAULT_TIMEOUT_SECONDS = 30.0
_USER_AGENT = "dogfoodandfun-social-automation/1.0"


def wp_client(*, timeout: float = _DEFAULT_TIMEOUT_SECONDS) -> httpx.Client:
    """Construct an authenticated httpx.Client for the WP REST API.

    Args:
        timeout: Per-request timeout in seconds. Default 30.

    Returns:
        An open `httpx.Client`. Caller MUST close it — use as a
        context manager (`with wp_client() as c: ...`).

    Raises:
        ConfigurationError: If `WP_URL`, `WP_USER`, or `WP_APP_PASSWORD`
            is unset.
    """
    base = os.environ.get("WP_URL", "").rstrip("/")
    user = os.environ.get("WP_USER", "")
    password = os.environ.get("WP_APP_PASSWORD", "")
    missing = [
        name
        for name, value in (
            ("WP_URL", base),
            ("WP_USER", user),
            ("WP_APP_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        raise ConfigurationError(
            f"WP REST client requires env vars: {', '.join(missing)}",
            context={"missing": missing},
        )
    return httpx.Client(
        base_url=base,
        auth=(user, password),
        timeout=timeout,
        headers={"User-Agent": _USER_AGENT},
    )
