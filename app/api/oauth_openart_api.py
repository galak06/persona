"""FastAPI routes for the OpenArt OAuth web flow.

  GET /api/v1/oauth/openart/start     → 302 to OpenArt's consent screen
  GET /api/v1/oauth/openart/callback  → exchange code, save token, 302 back
                                        to the frontend Reels page

Mounted under `/api/v1/oauth` next to `api.oauth_api` (Facebook), and
mirrors its patterns: an in-memory state stash for the cross-request leg
(single-process API; a restart just means clicking Authorize again) and
`OAUTH_REDIRECT_BASE_URL` for the absolute callback URL OpenArt redirects
to. The heavy lifting -- discovery, DCR, PKCE, exchange, storage -- lives in
`lib.oauth.openart_web`.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from lib.oauth.openart import openart_enabled
from lib.oauth.openart_web import OpenArtWebFlowError, begin_authorization, complete_authorization

router = APIRouter()

# state -> {pending: PendingAuthorization, brand_id, return_to, created_at}.
# Same in-memory CSRF pattern as oauth_api._pending_states, plus the PKCE
# verifier + token endpoint the callback leg needs.
_pending: dict[str, dict[str, Any]] = {}
_PENDING_TTL_SECONDS = 600

# Only send the browser back to local frontends (mirrors the API's CORS
# origin policy) -- anything else would be an open redirect.
_RETURN_TO_RE = re.compile(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?(/|$)")
_DEFAULT_RETURN_TO = "http://localhost:3000/reels"


def _callback_redirect_uri() -> str:
    base = os.environ.get("OAUTH_REDIRECT_BASE_URL", "http://localhost:5001").rstrip("/")
    return f"{base}/api/v1/oauth/openart/callback"


def _safe_return_to(raw: str) -> str:
    return raw if _RETURN_TO_RE.match(raw) else _DEFAULT_RETURN_TO


def _prune_pending() -> None:
    cutoff = time.time() - _PENDING_TTL_SECONDS
    for state in [s for s, entry in _pending.items() if entry["created_at"] < cutoff]:
        _pending.pop(state, None)


@router.get("/openart/start", summary="Start OpenArt OAuth flow")
def start_openart_oauth(
    brand_id: str = Query(..., description="Brand this authorization belongs to"),
    return_to: str = Query(
        default=_DEFAULT_RETURN_TO,
        description="Frontend URL to land on after the callback (local origins only)",
    ),
) -> RedirectResponse:
    """Redirect the operator to OpenArt's consent screen."""
    if not openart_enabled():
        raise HTTPException(
            status_code=503,
            detail={
                "code": "openart_not_configured",
                "message": "OpenArt is not configured for this brand -- enable it under "
                "integrations.openart in the brand's config.json first.",
            },
        )
    try:
        with httpx.Client() as http:
            pending = begin_authorization(http, _callback_redirect_uri(), brand_id)
    except OpenArtWebFlowError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    _prune_pending()
    _pending[pending.state] = {
        "pending": pending,
        "brand_id": brand_id,
        "return_to": _safe_return_to(return_to),
        "created_at": time.time(),
    }
    return RedirectResponse(url=pending.authorization_url)


@router.get("/openart/callback", summary="OpenArt OAuth callback")
def openart_callback(
    code: str = Query(default=""),
    state: str = Query(default=""),
    error: str = Query(default=""),
    error_description: str = Query(default=""),
) -> RedirectResponse:
    """Exchange the code with our saved PKCE verifier, save the token, and
    send the browser back to the Reels page with a status query param."""
    entry = _pending.pop(state, None) if state else None
    if entry is None:
        # Expired/unknown state: no stash to trust, land on the default page.
        return RedirectResponse(
            url=f"{_DEFAULT_RETURN_TO}?openart=error&reason={quote('invalid or expired state')}"
        )
    return_to: str = entry["return_to"]

    if error:
        reason = f"{error}: {error_description}" if error_description else error
        return RedirectResponse(url=f"{return_to}?openart=error&reason={quote(reason[:200])}")
    if not code:
        return RedirectResponse(
            url=f"{return_to}?openart=error&reason={quote('no authorization code returned')}"
        )

    try:
        with httpx.Client() as http:
            complete_authorization(
                http,
                entry["pending"],
                code=code,
                redirect_uri=_callback_redirect_uri(),
                brand_id=entry["brand_id"],
            )
    except OpenArtWebFlowError as exc:
        return RedirectResponse(url=f"{return_to}?openart=error&reason={quote(str(exc)[:200])}")

    return RedirectResponse(url=f"{return_to}?openart=connected")
