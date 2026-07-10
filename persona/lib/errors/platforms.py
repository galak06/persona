"""Platform-specific API error code taxonomy.

Sourced from Postiz's instagram.provider.ts and facebook.provider.ts —
maps raw platform error codes to our three action categories:

  "refresh-token" — credential is invalid/expired → re-authenticate
  "bad-body"      — content rejected by platform → fix content, don't retry
  "retry"         — transient server-side problem → back off and retry

Usage::

    from lib.errors.platforms import classify_fb_error, classify_ig_error

    action, message = classify_ig_error(subcode=2207042)
    # → ("bad-body", "Instagram daily post limit (25/day) reached")

    action, message = classify_fb_error(code=190)
    # → ("refresh-token", "Facebook access token expired or invalid")
"""

from __future__ import annotations

from typing import Literal

Action = Literal["refresh-token", "bad-body", "retry"]

# ── Instagram error subcodes ───────────────────────────────────────────────────
# Source: instagram.provider.ts handleErrors() — subcodes on error_subcode field

_IG_SUBCODE: dict[int, tuple[Action, str]] = {
    # Auth / session
    2207004: ("refresh-token", "Instagram account restricted — re-authenticate"),
    2207006: ("refresh-token", "Instagram session invalid — re-authenticate"),
    # Content problems (bad-body = fix content, do not retry)
    2207001: ("bad-body", "Instagram flagged comment/caption as spam"),
    2207009: ("bad-body", "Caption too long for Instagram"),
    2207010: ("bad-body", "Too many hashtags in caption (max 30)"),
    2207023: ("bad-body", "Invalid or unsupported media format"),
    2207026: ("bad-body", "Account not eligible to publish Reels"),
    2207042: ("bad-body", "Instagram daily post limit reached (25 posts/day)"),
    2207050: ("bad-body", "Invalid collaborator handle in post"),
    2207051: ("bad-body", "Collaborator account not found or private"),
    2207057: ("bad-body", "Reel processing failed — re-upload the video"),
    # Transient (retry)
    2207003: ("retry", "Instagram temporary rate limit — retry in 15 min"),
    2207028: ("retry", "Media container not ready yet — retry in 30s"),
}

# IG top-level error codes (when subcode is absent)
_IG_CODE: dict[int, tuple[Action, str]] = {
    190: ("refresh-token", "Instagram access token expired or invalid"),
    200: ("refresh-token", "Insufficient Instagram permissions — re-authenticate"),
    100: ("bad-body", "Instagram API parameter invalid"),
    368: ("bad-body", "Content violates Instagram community standards"),
    2207: ("bad-body", "Instagram media publish error"),
}

# ── Facebook error codes ───────────────────────────────────────────────────────
# Source: facebook.provider.ts handleErrors()

_FB_CODE: dict[int, tuple[Action, str]] = {
    # Auth
    190: ("refresh-token", "Facebook access token expired or invalid"),
    200: ("refresh-token", "Insufficient Facebook permissions — re-authenticate"),
    102: ("refresh-token", "Facebook session expired — re-authenticate"),
    # Content problems
    1346003: ("bad-body", "Facebook Page is not published — publish the page first"),
    1366046: ("bad-body", "Content blocked by Facebook — violates community standards"),
    1390008: ("bad-body", "Account temporarily blocked from posting — wait 24h"),
    1404006: ("bad-body", "Post already published — duplicate content"),
    1404102: ("bad-body", "Media upload failed — check file format and size"),
    1404112: ("bad-body", "Video processing failed — re-upload the video"),
    1609010: ("bad-body", "Invalid link in post — check the URL"),
    368: ("bad-body", "Content violates Facebook community standards"),
    506: ("bad-body", "Duplicate post — Facebook rejected identical content"),
    # Transient
    1: ("retry", "Facebook API unknown error — retry"),
    2: ("retry", "Facebook API service temporarily unavailable"),
    4: ("retry", "Facebook API rate limit — retry in 10 min"),
    17: ("retry", "Facebook API user rate limit — retry in 60 min"),
    32: ("retry", "Facebook Page API rate limit — retry"),
    613: ("retry", "Facebook calls to this API have exceeded the rate limit"),
}

# ── Public classifiers ─────────────────────────────────────────────────────────


def classify_ig_error(
    *,
    code: int | None = None,
    subcode: int | None = None,
    message: str = "",
) -> tuple[Action, str]:
    """Classify an Instagram Graph API error.

    Checks subcode first (more specific), then code, then falls back
    to heuristics on the message string.

    Returns:
        (action, human_readable_message)
    """
    if subcode and subcode in _IG_SUBCODE:
        return _IG_SUBCODE[subcode]
    if code and code in _IG_CODE:
        return _IG_CODE[code]

    # Heuristic fallbacks
    msg_lower = message.lower()
    if "token" in msg_lower or "oauth" in msg_lower or "session" in msg_lower:
        return ("refresh-token", f"Instagram auth error: {message}")
    if "rate" in msg_lower or "limit" in msg_lower or "too many" in msg_lower:
        return ("retry", f"Instagram rate limit: {message}")

    return ("retry", f"Instagram API error (code={code}, subcode={subcode}): {message}")


def classify_fb_error(
    *,
    code: int | None = None,
    subcode: int | None = None,
    message: str = "",
) -> tuple[Action, str]:
    """Classify a Facebook Graph API error.

    Returns:
        (action, human_readable_message)
    """
    if code and code in _FB_CODE:
        return _FB_CODE[code]

    # Subcode as code sometimes (FB reuses the field)
    if subcode and subcode in _FB_CODE:
        return _FB_CODE[subcode]

    msg_lower = message.lower()
    if "token" in msg_lower or "oauth" in msg_lower or "session" in msg_lower:
        return ("refresh-token", f"Facebook auth error: {message}")
    if "rate" in msg_lower or "limit" in msg_lower or "too many" in msg_lower:
        return ("retry", f"Facebook rate limit: {message}")
    if "spam" in msg_lower or "block" in msg_lower or "violat" in msg_lower:
        return ("bad-body", f"Facebook content policy: {message}")

    return ("retry", f"Facebook API error (code={code}, subcode={subcode}): {message}")


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def parse_graph_error(body: dict[str, object]) -> tuple[int | None, int | None, str]:
    """Extract (code, subcode, message) from a Graph API error response body."""
    nested = body.get("error")
    err: dict[str, object] = nested if isinstance(nested, dict) else body
    return (
        _as_int(err.get("code")),
        _as_int(err.get("error_subcode")) or _as_int(err.get("subcode")),
        str(err.get("message", "")),
    )
