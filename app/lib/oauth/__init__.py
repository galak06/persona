"""OAuth token management for Persona.

Handles Facebook and Instagram OAuth 2.0 flows:
- Authorization URL generation
- Code → access token exchange
- Long-lived token exchange (60-day tokens)
- Automatic token refresh before expiry
- Supabase-backed token storage with fallback to JSON file
"""

from lib.oauth.facebook import (
    FacebookOAuth,
    OAuthToken,
    exchange_code_for_token,
    get_authorization_url,
    refresh_long_lived_token,
)
from lib.oauth.store import TokenStore

__all__ = [
    "FacebookOAuth",
    "OAuthToken",
    "TokenStore",
    "exchange_code_for_token",
    "get_authorization_url",
    "refresh_long_lived_token",
]
