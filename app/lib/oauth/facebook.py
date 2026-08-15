"""Facebook + Instagram OAuth 2.0 flow for Persona.

Facebook uses a two-step token model:
  1. Short-lived user token  (1 hour)  — from OAuth code exchange
  2. Long-lived user token   (60 days) — exchanged from short-lived
  3. Page access token       (never expires while token is valid) — fetched
     directly from the page node (GET /{page_id}?fields=access_token)

Instagram uses the same page token (FB_PAGE_TOKEN) — no separate OAuth needed.

Typical setup flow:
  1. Direct user to get_authorization_url()
  2. User grants permissions, FB redirects to your callback URL with ?code=...
  3. Call exchange_code_for_token(code) → short-lived token
  4. Call exchange_for_long_lived_token(short_token) → long-lived token
  5. Call get_page_token(long_lived_token, page_id) → permanent page token
  6. Store page token via TokenStore — now used for all Graph API calls

Refresh strategy:
  Long-lived tokens last 60 days. Call refresh_long_lived_token() any time
  before expiry to get a fresh 60-day token. Run weekly via scheduler.

Environment variables required:
    FB_APP_ID         — From developers.facebook.com → Your App → Settings → Basic
    FB_APP_SECRET     — Same location
    OAUTH_REDIRECT_BASE_URL — e.g. https://yourdomain.com or http://localhost:5001
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from urllib.parse import urlencode

import httpx

# ── Config ────────────────────────────────────────────────────────────────────

GRAPH_API = "https://graph.facebook.com/v23.0"
OAUTH_DIALOG = "https://www.facebook.com/v23.0/dialog/oauth"

# Permissions needed for the full Persona pipeline:
#   pages_manage_posts        — publish to page; also what the /video_reels
#                                endpoint (Reel publishing) actually requires,
#                                alongside pages_read_engagement/pages_show_list
#   pages_read_engagement     — read page comments
#   pages_show_list           — list pages the user manages
#   instagram_basic           — read IG account
#   instagram_content_publish — publish to IG
#   groups_access_member_info — (limited) read group members
#
# `publish_video` deliberately NOT requested: confirmed live against a real
# Meta app -- it doesn't appear in the app's Manage-Pages permissions table
# at all (unlike pages_manage_posts, which showed a real "Add" button), and
# requesting it makes the consent dialog fail before ever reaching the
# redirect step (a misleading "Can't load URL" domain-mismatch screen was
# actually that failure's teardown page, not a real redirect-URI problem).
DEFAULT_SCOPES = [
    "pages_manage_posts",
    "pages_read_engagement",
    "pages_show_list",
    "instagram_basic",
    "instagram_content_publish",
    "public_profile",
]

# Refresh when fewer than this many days remain on the token
REFRESH_THRESHOLD_DAYS = 10


# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class OAuthToken:
    access_token: str
    token_type: str = "bearer"       # "bearer" | "page"
    expires_at: datetime | None = None  # None = non-expiring page token
    platform: str = "facebook"
    token_id: str = ""               # page_id or ig_account_id
    scope: list[str] = field(default_factory=list)

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) >= self.expires_at

    @property
    def needs_refresh(self) -> bool:
        if self.expires_at is None:
            return False
        threshold = datetime.now(timezone.utc) + timedelta(days=REFRESH_THRESHOLD_DAYS)
        return threshold >= self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "token_type": self.token_type,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "platform": self.platform,
            "token_id": self.token_id,
            "scope": self.scope,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OAuthToken":
        expires_at = None
        if data.get("expires_at"):
            expires_at = datetime.fromisoformat(data["expires_at"])
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
        return cls(
            access_token=data["access_token"],
            token_type=data.get("token_type", "bearer"),
            expires_at=expires_at,
            platform=data.get("platform", "facebook"),
            token_id=data.get("token_id", ""),
            scope=data.get("scope", []),
        )


# ── Main class ────────────────────────────────────────────────────────────────


class FacebookOAuth:
    """Manages the full FB/IG OAuth lifecycle."""

    def __init__(
        self,
        app_id: str | None = None,
        app_secret: str | None = None,
        redirect_base_url: str | None = None,
    ) -> None:
        self.app_id = app_id or os.environ["FB_APP_ID"]
        self.app_secret = app_secret or os.environ["FB_APP_SECRET"]
        self.redirect_base_url = (
            redirect_base_url
            or os.environ.get("OAUTH_REDIRECT_BASE_URL", "http://localhost:5001")
        ).rstrip("/")
        self.redirect_uri = f"{self.redirect_base_url}/api/v1/oauth/facebook/callback"

    def get_authorization_url(self, state: str = "", scopes: list[str] | None = None) -> str:
        """Return the URL to redirect the user to for FB OAuth consent."""
        params = {
            "client_id": self.app_id,
            "redirect_uri": self.redirect_uri,
            "scope": ",".join(scopes or DEFAULT_SCOPES),
            "response_type": "code",
        }
        if state:
            params["state"] = state
        return f"{OAUTH_DIALOG}?{urlencode(params)}"

    def exchange_code(self, code: str) -> OAuthToken:
        """Exchange an authorization code for a short-lived user token."""
        r = httpx.get(
            f"{GRAPH_API}/oauth/access_token",
            params={
                "client_id": self.app_id,
                "client_secret": self.app_secret,
                "redirect_uri": self.redirect_uri,
                "code": code,
            },
            timeout=15.0,
        )
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise ValueError(f"FB OAuth error: {data['error']}")

        expires_in = data.get("expires_in", 3600)
        return OAuthToken(
            access_token=data["access_token"],
            token_type="bearer",
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
        )

    def exchange_for_long_lived(self, short_token: str) -> OAuthToken:
        """Exchange a short-lived token for a 60-day long-lived user token."""
        r = httpx.get(
            f"{GRAPH_API}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": self.app_id,
                "client_secret": self.app_secret,
                "fb_exchange_token": short_token,
            },
            timeout=15.0,
        )
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise ValueError(f"FB long-lived exchange error: {data['error']}")

        expires_in = data.get("expires_in", 60 * 86400)
        return OAuthToken(
            access_token=data["access_token"],
            token_type="bearer",
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
        )

    def get_page_token(self, user_token: str, page_id: str) -> OAuthToken:
        """Exchange a user token for a never-expiring page access token.

        Queries the page node directly instead of listing /me/accounts:
        for business-owned pages Meta can return an empty list from
        /me/accounts even when the token's granular scopes explicitly
        authorize the page (confirmed live 2026-08-09) — direct node
        access works in exactly that case.
        """
        r = httpx.get(
            f"{GRAPH_API}/{page_id}",
            params={"access_token": user_token, "fields": "id,name,access_token"},
            timeout=15.0,
        )
        r.raise_for_status()
        data = r.json()

        if "access_token" not in data:
            raise ValueError(
                f"Page {page_id} returned no access_token "
                f"(check the token's granular page scopes): {data}"
            )
        return OAuthToken(
            access_token=data["access_token"],
            token_type="page",
            expires_at=None,  # page tokens don't expire
            platform="facebook",
            token_id=page_id,
        )

    def refresh_long_lived(self, token: OAuthToken) -> OAuthToken:
        """Refresh a long-lived token (extend its 60-day window).

        Facebook allows refreshing any time before expiry — just exchange again.
        Page tokens don't expire; only user tokens need refreshing.
        """
        if token.token_type == "page":
            # Page tokens don't expire — nothing to do
            return token
        return self.exchange_for_long_lived(token.access_token)

    def full_setup_flow(self, code: str, page_id: str) -> dict[str, OAuthToken]:
        """Run the complete token setup: code → short → long-lived → page token.

        Returns dict with keys: 'user_token', 'page_token'
        """
        short = self.exchange_code(code)
        long_lived = self.exchange_for_long_lived(short.access_token)
        page = self.get_page_token(long_lived.access_token, page_id)
        return {"user_token": long_lived, "page_token": page}

    def debug_token(self, token: str) -> dict[str, Any]:
        """Call /debug_token to inspect a token's validity and permissions."""
        app_token = f"{self.app_id}|{self.app_secret}"
        r = httpx.get(
            f"{GRAPH_API}/debug_token",
            params={"input_token": token, "access_token": app_token},
            timeout=15.0,
        )
        r.raise_for_status()
        return cast(dict[str, Any], r.json().get("data", {}))


# ── Module-level convenience functions ────────────────────────────────────────


def get_authorization_url(state: str = "", scopes: list[str] | None = None) -> str:
    return FacebookOAuth().get_authorization_url(state=state, scopes=scopes)


def exchange_code_for_token(code: str) -> OAuthToken:
    oauth = FacebookOAuth()
    short = oauth.exchange_code(code)
    return oauth.exchange_for_long_lived(short.access_token)


def refresh_long_lived_token(token: OAuthToken) -> OAuthToken:
    return FacebookOAuth().refresh_long_lived(token)
