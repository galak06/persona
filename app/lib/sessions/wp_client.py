"""WordPress REST API client — single httpx.Client factory.

Replaces 4 inline implementations across:
    - scripts/comment_poster.py:94-97
    - scripts/wp_scan.py:84-93
    - recipe-publisher/publishers/wordpress.py:41-52 (with alternate env names)
    - scripts/content_pipeline.py:147-152 (used `requests`, not `httpx`!)

Standardizes the env-var contract on `WP_URL` / `WP_USER` /
`WP_APP_PASSWORD`. The legacy `WP_BASE_URL` / `WP_APP_PASSWORD_USER`
aliases were removed in Stage 4.

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
import time

import httpx

from lib.errors.configuration import ConfigurationError
from lib.runtime.health_check import HealthCheckResult

_DEFAULT_TIMEOUT_SECONDS = 30.0
_USER_AGENT = "persona-social-automation/1.0"


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


def wp_health_probe() -> HealthCheckResult:
    """Health probe: WP REST is reachable AND Application Passwords actually
    authenticate -- not just that the site responds.

    Reproduces, as an automated check, the manual diagnosis from a real
    incident (2026-08-06, dogfoodandfun): the site was fully reachable and
    `WP_URL`/`WP_USER`/`WP_APP_PASSWORD` were all set and correct, but every
    authenticated call still 401'd for weeks because a HOSTING-LEVEL toggle
    (Hostinger's Tools -> Security -> "Disable application passwords" --
    unrelated to any WordPress plugin) silently disabled the feature
    site-wide. `/wp-json/`'s `authentication` field is the tell: it's an
    empty list when this happens, vs. a real `application-passwords` entry
    when the feature is actually available. Checking that field first turns
    a generic 401 into an actionable diagnostic instead of a guessing game.
    """
    started = time.monotonic()
    try:
        client = wp_client(timeout=10.0)
    except ConfigurationError as exc:
        return HealthCheckResult("wp", False, str(exc))

    try:
        with client:
            root = client.get("/wp-json/")
            latency_ms = int((time.monotonic() - started) * 1000)
            if root.status_code != 200:
                return HealthCheckResult(
                    "wp", False, f"/wp-json/ returned {root.status_code}", latency_ms
                )
            auth_methods = root.json().get("authentication") or {}
            if "application-passwords" not in auth_methods:
                return HealthCheckResult(
                    "wp",
                    False,
                    "Application Passwords not advertised by /wp-json/ -- usually a "
                    "hosting-level toggle (e.g. Hostinger Tools > Security > 'Disable "
                    "application passwords'), not a WordPress setting or a bad password",
                    latency_ms,
                )
            me = client.get("/wp-json/wp/v2/users/me")
            latency_ms = int((time.monotonic() - started) * 1000)
            if me.status_code != 200:
                return HealthCheckResult(
                    "wp",
                    False,
                    f"authenticated GET /users/me returned {me.status_code} despite "
                    "Application Passwords being available -- check WP_USER/WP_APP_PASSWORD "
                    "are correct and the application password hasn't been revoked",
                    latency_ms,
                )
            return HealthCheckResult(
                "wp", True, f"WP 200, authenticated as {me.json().get('name')}", latency_ms
            )
    except httpx.HTTPError as exc:
        return HealthCheckResult(
            "wp",
            False,
            f"network error: {type(exc).__name__}",
            int((time.monotonic() - started) * 1000),
        )
