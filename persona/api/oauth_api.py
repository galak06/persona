"""FastAPI OAuth routes for Persona.

Endpoints:
  GET  /api/v1/oauth/facebook                → redirect to FB consent screen
  GET  /api/v1/oauth/facebook/callback       → exchange code, store token
  GET  /api/v1/oauth/facebook/page/{page_id} → exchange user token → page token
  GET  /api/v1/oauth/tokens                  → list all stored tokens (redacted)
  POST /api/v1/oauth/facebook/refresh        → refresh expiring user token
  DELETE /api/v1/oauth/{platform}/{type}     → revoke/delete a stored token

Setup flow:
  1. Visit GET /api/v1/oauth/facebook — you'll be redirected to Facebook
  2. Grant permissions, you'll land back at /callback with a code
  3. The callback automatically exchanges code → long-lived user token and saves it
  4. Call GET /api/v1/oauth/facebook/page/{your_page_id} to get the page token
  5. Done — the API will use the stored page token for all Graph API calls

Mount in approval_api.py:
    from api.oauth_api import router as oauth_router
    app.include_router(oauth_router, prefix="/api/v1/oauth", tags=["oauth"])
"""

from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from lib.oauth.facebook import FacebookOAuth
from lib.oauth.store import TokenStore

router = APIRouter()

# ── Simple in-memory state store for CSRF protection ─────────────────────────
# In production, use Redis: store.set(state, "1", ex=600)
_pending_states: set[str] = set()


# ── Schemas ───────────────────────────────────────────────────────────────────


class TokenSummary(BaseModel):
    platform: str
    token_type: str
    token_id: str
    expires_at: str | None
    needs_refresh: bool


class RefreshResponse(BaseModel):
    platform: str
    token_type: str
    new_expires_at: str | None
    refreshed: bool


# ── Routes ────────────────────────────────────────────────────────────────────


@router.get("/facebook", summary="Start Facebook OAuth flow")
def start_facebook_oauth(
    scopes: str = Query(default="", description="Comma-separated extra scopes to request"),
) -> RedirectResponse:
    """Redirect the user to the Facebook consent screen."""
    oauth = FacebookOAuth()
    state = secrets.token_urlsafe(16)
    _pending_states.add(state)

    extra_scopes = [s.strip() for s in scopes.split(",") if s.strip()]
    url = oauth.get_authorization_url(state=state, scopes=extra_scopes or None)
    return RedirectResponse(url=url)


@router.get("/facebook/callback", summary="Facebook OAuth callback")
def facebook_callback(
    code: str = Query(..., description="Authorization code from Facebook"),
    state: str = Query(default="", description="CSRF state token"),
    error: str = Query(default="", description="Error from Facebook (if any)"),
    error_description: str = Query(default=""),
) -> JSONResponse:
    """Handle the Facebook OAuth redirect. Exchange code → token and save."""
    if error:
        raise HTTPException(
            status_code=400,
            detail=f"Facebook OAuth error: {error} — {error_description}",
        )

    if state and state not in _pending_states:
        raise HTTPException(status_code=400, detail="Invalid OAuth state (CSRF check failed)")
    _pending_states.discard(state)

    try:
        oauth = FacebookOAuth()
        token = oauth.exchange_code(code)
        long_lived = oauth.exchange_for_long_lived(token.access_token)
        long_lived.platform = "facebook"
        long_lived.token_type = "bearer"

        brand_id = os.environ.get("PERSONA_BRAND", "default")
        store = TokenStore(brand_id=brand_id)
        store.save(long_lived)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Token exchange failed: {exc}") from exc

    return JSONResponse(
        content={
            "status": "ok",
            "message": "Long-lived user token saved. Call /oauth/facebook/page/{page_id} to get the page token.",
            "expires_at": long_lived.expires_at.isoformat() if long_lived.expires_at else None,
            "needs_refresh": long_lived.needs_refresh,
        }
    )


@router.get("/facebook/page/{page_id}", summary="Exchange user token → page token")
def get_page_token(page_id: str) -> JSONResponse:
    """Exchange the stored user token for a (non-expiring) page access token."""
    brand_id = os.environ.get("PERSONA_BRAND", "default")
    store = TokenStore(brand_id=brand_id)

    user_token = store.load("facebook", "bearer")
    if not user_token:
        raise HTTPException(
            status_code=404,
            detail="No Facebook user token found. Run the OAuth flow first: GET /api/v1/oauth/facebook",
        )

    if user_token.is_expired:
        raise HTTPException(
            status_code=400,
            detail="Facebook user token has expired. Re-run the OAuth flow: GET /api/v1/oauth/facebook",
        )

    try:
        oauth = FacebookOAuth()
        page_token = oauth.get_page_token(user_token.access_token, page_id)
        store.save(page_token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Page token exchange failed: {exc}") from exc

    return JSONResponse(
        content={
            "status": "ok",
            "page_id": page_id,
            "message": "Page token saved. Update FB_PAGE_ID and FB_PAGE_TOKEN env vars (or the system will use the stored token automatically).",
            "token_type": "page",
            "expires_at": None,  # page tokens don't expire
        }
    )


@router.get("/tokens", summary="List stored tokens (access_token redacted)")
def list_tokens() -> JSONResponse:
    """Return a summary of all stored tokens without exposing access_token values."""
    brand_id = os.environ.get("PERSONA_BRAND", "default")
    store = TokenStore(brand_id=brand_id)
    tokens = store.list_all()

    summaries = []
    for t in tokens:
        from lib.oauth.facebook import OAuthToken
        token = store.load(
            t.get("platform", ""),
            t.get("token_type", ""),
            t.get("token_id", ""),
        )
        summaries.append(
            {
                "platform": t.get("platform"),
                "token_type": t.get("token_type"),
                "token_id": t.get("token_id"),
                "expires_at": t.get("expires_at"),
                "needs_refresh": token.needs_refresh if token else False,
                "is_expired": token.is_expired if token else True,
            }
        )

    return JSONResponse(content={"brand_id": brand_id, "tokens": summaries})


@router.post("/facebook/refresh", summary="Refresh expiring Facebook user token")
def refresh_facebook_token() -> RefreshResponse:
    """Extend the 60-day user token window. Safe to call any time before expiry."""
    brand_id = os.environ.get("PERSONA_BRAND", "default")
    store = TokenStore(brand_id=brand_id)

    token = store.load("facebook", "bearer")
    if not token:
        raise HTTPException(status_code=404, detail="No Facebook user token stored")

    if token.is_expired:
        raise HTTPException(
            status_code=400,
            detail="Token already expired — full re-auth required: GET /api/v1/oauth/facebook",
        )

    if not token.needs_refresh:
        return RefreshResponse(
            platform="facebook",
            token_type="bearer",
            new_expires_at=token.expires_at.isoformat() if token.expires_at else None,
            refreshed=False,
        )

    try:
        from lib.oauth.facebook import refresh_long_lived_token
        refreshed = refresh_long_lived_token(token)
        store.save(refreshed)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Token refresh failed: {exc}") from exc

    return RefreshResponse(
        platform="facebook",
        token_type="bearer",
        new_expires_at=refreshed.expires_at.isoformat() if refreshed.expires_at else None,
        refreshed=True,
    )


@router.delete("/{platform}/{token_type}", summary="Delete a stored token")
def delete_token(platform: str, token_type: str, token_id: str = "") -> JSONResponse:
    """Remove a stored token. Does NOT revoke it on the platform side."""
    brand_id = os.environ.get("PERSONA_BRAND", "default")
    store = TokenStore(brand_id=brand_id)
    store.delete(platform, token_type, token_id)
    return JSONResponse(content={"status": "deleted", "platform": platform, "token_type": token_type})


@router.get("/facebook/debug", summary="Inspect a stored token's validity")
def debug_facebook_token(token_type: str = "page", token_id: str = "") -> JSONResponse:
    """Call the FB /debug_token endpoint to inspect permissions and expiry."""
    brand_id = os.environ.get("PERSONA_BRAND", "default")
    store = TokenStore(brand_id=brand_id)
    token = store.load("facebook", token_type, token_id)
    if not token:
        raise HTTPException(status_code=404, detail="Token not found in store")

    try:
        oauth = FacebookOAuth()
        debug_info = oauth.debug_token(token.access_token)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Debug call failed: {exc}") from exc

    return JSONResponse(content=debug_info)
