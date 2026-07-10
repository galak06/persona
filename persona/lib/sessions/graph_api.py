"""Facebook / Instagram Graph API URL composition, token resolution, and resilient fetch.

Single source of truth for:
    - The Graph API version (`v23.0`)
    - The token-fallback contract
    - Resilient HTTP requests: auto-refresh on 401, backoff on 429/5xx,
      error code classification via lib.errors.platforms
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from lib.errors.auth import AuthError, TokenExpiredError
from lib.errors.configuration import ConfigurationError
from lib.errors.network import NetworkError
from lib.errors.platforms import classify_fb_error, classify_ig_error, parse_graph_error
from lib.errors.rate_limit import RateLimitedError

log = logging.getLogger(__name__)

GRAPH_VERSION = "v23.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"

_MAX_RETRIES = 3
_RETRY_SLEEP = 5        # seconds between 5xx retries
_RATE_LIMIT_SLEEP = 60  # seconds to wait on 429


def graph_url(path: str) -> str:
    """Compose a Graph API URL for the configured version."""
    return f"{GRAPH_BASE}/{path.lstrip('/')}"


def read_fb_token() -> str:
    """Return the Facebook Page access token, preferring TokenStore over env var."""
    # Try TokenStore first (set by OAuth flow)
    token = _token_from_store("facebook", "page")
    if token:
        return token
    # Fall back to env var (manually set)
    token = os.environ.get("FB_PAGE_TOKEN", "").strip()
    if not token:
        raise ConfigurationError("FB_PAGE_TOKEN env var is required but unset/empty")
    return token


def read_ig_token() -> str:
    """Return the IG Graph API token (same page token works for IG Business)."""
    token = _token_from_store("facebook", "page") or _token_from_store("instagram", "page")
    if token:
        return token
    token = (
        os.environ.get("FB_PAGE_TOKEN", "").strip()
        or os.environ.get("IG_GRAPH_ACCESS_TOKEN", "").strip()
    )
    if not token:
        raise ConfigurationError(
            "FB_PAGE_TOKEN (preferred) or IG_GRAPH_ACCESS_TOKEN env var is required"
        )
    return token


def _token_from_store(platform: str, token_type: str) -> str | None:
    """Load a token from TokenStore. Returns None if unavailable or expired."""
    try:
        from lib.oauth.store import TokenStore
        store = TokenStore()
        token = store.load(platform, token_type)
        if token and not token.is_expired:
            return token.access_token
    except Exception:
        pass
    return None


def _refresh_token(platform: str) -> str | None:
    """Attempt to refresh the stored token. Returns new access_token or None."""
    try:
        from lib.oauth.facebook import FacebookOAuth
        from lib.oauth.store import TokenStore
        store = TokenStore()
        user_token = store.load(platform, "bearer")
        if not user_token or user_token.is_expired:
            return None
        oauth = FacebookOAuth()
        refreshed = oauth.refresh_long_lived(user_token)
        store.save(refreshed)
        log.info("graph_api: token refreshed, new expiry=%s", refreshed.expires_at)
        return refreshed.access_token
    except Exception as e:
        log.warning("graph_api: token refresh failed: %s", e)
        return None


def graph_request(
    method: str,
    path: str,
    *,
    platform: str = "facebook",
    token: str | None = None,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
    files: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Make a resilient Graph API request.

    Retry behaviour (from Postiz's SocialAbstract.fetch pattern):
      - 401 / token error → refresh token once, retry
      - 429               → sleep ``_RATE_LIMIT_SLEEP`` s, retry up to 3×
      - 5xx               → sleep ``_RETRY_SLEEP`` s, retry up to 3×
      - 4xx (other)       → classify error code, raise immediately (no retry)

    Args:
        method:   HTTP method ("GET", "POST", "DELETE").
        path:     Graph API path, e.g. "me/feed" or "/{page_id}/media".
        platform: "facebook" or "instagram" — controls error classifier.
        token:    Access token. If omitted, resolved from TokenStore / env.
        params:   URL query parameters (merged with access_token).
        json:     JSON request body (for POST).
        files:    Multipart files (for media upload).
        timeout:  Request timeout in seconds.

    Returns:
        Parsed JSON response body as dict.

    Raises:
        TokenExpiredError:  401 that couldn't be fixed by refresh.
        RateLimitedError:   429 after exhausting retries.
        AuthError:          Permanent auth failure (bad scope, revoked).
        NetworkError:       Timeout or 5xx after exhausting retries.
        ValueError:         4xx content error (bad-body) — fix and retry manually.
    """
    classify = classify_ig_error if platform == "instagram" else classify_fb_error
    _token = token or (read_ig_token() if platform == "instagram" else read_fb_token())
    url = graph_url(path)

    for attempt in range(1, _MAX_RETRIES + 1):
        _params = dict(params or {})
        _params["access_token"] = _token

        try:
            r = httpx.request(
                method.upper(),
                url,
                params=_params if method.upper() == "GET" else None,
                data=_params if method.upper() != "GET" and files else None,
                json=json if method.upper() != "GET" and not files else None,
                files=files,
                timeout=timeout,
            )
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            if attempt < _MAX_RETRIES:
                log.warning("graph_api: network error (attempt %d): %s", attempt, e)
                time.sleep(_RETRY_SLEEP * attempt)
                continue
            raise NetworkError(f"Graph API network error after {attempt} attempts: {e}") from e

        # ── 429 rate limit ────────────────────────────────────────────────────
        if r.status_code == 429:
            retry_after = int(r.headers.get("Retry-After", _RATE_LIMIT_SLEEP))
            log.warning("graph_api: 429 rate limit (attempt %d), sleeping %ds", attempt, retry_after)
            if attempt < _MAX_RETRIES:
                time.sleep(retry_after)
                continue
            raise RateLimitedError(
                f"Graph API rate limited after {attempt} attempts",
                retry_after_seconds=retry_after,
            )

        # ── 5xx transient ─────────────────────────────────────────────────────
        if r.status_code >= 500:
            log.warning("graph_api: %d server error (attempt %d)", r.status_code, attempt)
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_SLEEP * attempt)
                continue
            raise NetworkError(f"Graph API {r.status_code} after {attempt} attempts: {r.text[:200]}")

        # ── 401 / token error — try refresh once ──────────────────────────────
        if r.status_code == 401 or (
            r.status_code == 400 and "OAuthException" in r.text
        ):
            if attempt == 1 and not token:  # only auto-refresh on first attempt
                log.info("graph_api: 401 — attempting token refresh")
                new_token = _refresh_token(platform)
                if new_token:
                    _token = new_token
                    continue
            # Classify the error
            try:
                body = r.json()
            except Exception:
                body = {}
            code, subcode, msg = parse_graph_error(body)
            action, human_msg = classify(code=code, subcode=subcode, message=msg)
            if action == "refresh-token":
                raise TokenExpiredError(human_msg)
            raise AuthError(human_msg)

        # ── 4xx content error ─────────────────────────────────────────────────
        if r.status_code >= 400:
            try:
                body = r.json()
            except Exception:
                body = {}
            code, subcode, msg = parse_graph_error(body)
            action, human_msg = classify(code=code, subcode=subcode, message=msg)
            log.warning("graph_api: %d error — action=%s msg=%s", r.status_code, action, human_msg)
            if action == "retry" and attempt < _MAX_RETRIES:
                time.sleep(_RETRY_SLEEP * attempt)
                continue
            if action == "refresh-token":
                raise TokenExpiredError(human_msg)
            raise ValueError(f"[{action}] {human_msg}")

        # ── Success ───────────────────────────────────────────────────────────
        try:
            return r.json()  # type: ignore[return-value]
        except Exception:
            return {"raw": r.text}

    raise NetworkError(f"Graph API request failed after {_MAX_RETRIES} attempts")
