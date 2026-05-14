"""North-America-likely heuristic for IG follow candidates.

Three-valued: True | False | None. None means "no signal either way" —
the caller decides whether to accept-by-default or reject-on-unknown.
Default policy in the scout pipeline is accept-on-None: we'd rather
follow some non-NA users than starve the follow queue.

The heuristic is intentionally cheap (regex + literal matches over a
short bio string). No web requests, no language-detection models.

Why this is a heuristic and not a hard filter: Instagram does not
expose user country on public profiles. We're inferring from public bio
text alone. False positives (e.g., a UK dog account using "London,
Ontario" geo-cues) are accepted as cost-of-doing-business.
"""

from __future__ import annotations

import re
import unicodedata

_US_STATE_ABBREVS: frozenset[str] = frozenset({
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
})

_CA_PROVINCE_ABBREVS: frozenset[str] = frozenset({
    "AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON",
    "PE", "QC", "SK", "YT",
})

_NA_CITY_LITERALS: frozenset[str] = frozenset({
    "nyc", "new york", "brooklyn", "manhattan", "queens", "bronx",
    "los angeles", "la, ca", "san francisco", "sf bay", "bay area",
    "chicago", "houston", "phoenix", "philadelphia", "san diego",
    "dallas", "austin", "seattle", "denver", "boston", "atlanta",
    "miami", "portland", "nashville", "minneapolis", "detroit",
    "toronto", "montreal", "vancouver", "calgary", "edmonton",
    "ottawa", "winnipeg", "quebec", "halifax",
})

_NA_KEYWORD_LITERALS: frozenset[str] = frozenset({
    "usa", "u.s.a", "united states", "american", "canada", "canadian",
})

# Country flag emojis we want to count as negative signals. Excludes 🇺🇸 and 🇨🇦.
_NEGATIVE_FLAG_EMOJIS: frozenset[str] = frozenset({
    "🇮🇱", "🇩🇪", "🇫🇷", "🇮🇹", "🇪🇸", "🇵🇹", "🇳🇱", "🇧🇪", "🇨🇭",
    "🇦🇹", "🇸🇪", "🇳🇴", "🇩🇰", "🇫🇮", "🇮🇪", "🇬🇧", "🇵🇱", "🇨🇿",
    "🇬🇷", "🇹🇷", "🇷🇺", "🇺🇦", "🇮🇳", "🇨🇳", "🇯🇵", "🇰🇷", "🇹🇭",
    "🇻🇳", "🇮🇩", "🇵🇭", "🇲🇾", "🇸🇬", "🇦🇺", "🇳🇿", "🇧🇷", "🇦🇷",
    "🇲🇽", "🇿🇦", "🇪🇬", "🇸🇦", "🇦🇪",
})

_POSITIVE_FLAG_EMOJIS: frozenset[str] = frozenset({"🇺🇸", "🇨🇦"})

# Tokens like ", CA" / ", NY" / ", ON" — a state/province abbreviation
# preceded by a comma is a much stronger signal than the bare two-letter
# string (which collides with words like "BC" in "BC means before Christ").
_COMMA_STATE_RE = re.compile(r",\s*([A-Z]{2})\b")


def _has_non_latin_script(text: str) -> bool:
    """True if any character is in a non-Latin script block.

    Captures Hebrew, Arabic, Cyrillic, Greek, CJK, Devanagari, etc.
    Punctuation, emojis, and ASCII pass through as Latin/neutral.
    """
    for ch in text:
        if ch.isascii() or not ch.isalpha():
            continue
        try:
            name = unicodedata.name(ch)
        except ValueError:
            continue
        if "LATIN" not in name:
            return True
    return False


def _positive_score(text: str) -> int:
    """Count NA-aligned signals in `text`."""
    lower = text.lower()
    score = 0

    for keyword in _NA_KEYWORD_LITERALS:
        if keyword in lower:
            score += 1

    for city in _NA_CITY_LITERALS:
        if city in lower:
            score += 1

    for match in _COMMA_STATE_RE.findall(text):
        if match in _US_STATE_ABBREVS or match in _CA_PROVINCE_ABBREVS:
            score += 2

    for flag in _POSITIVE_FLAG_EMOJIS:
        if flag in text:
            score += 2

    return score


def _negative_score(text: str) -> int:
    """Count anti-NA signals in `text`."""
    score = 0
    if _has_non_latin_script(text):
        score += 3
    for flag in _NEGATIVE_FLAG_EMOJIS:
        if flag in text:
            score += 2
    return score


def is_north_america_likely(
    bio: str | None,
    display_name: str | None = None,
) -> bool | None:
    """Return True/False/None for "this candidate appears to be NA-based."

    Args:
        bio: Public bio text scraped from the profile. None if unscraped.
        display_name: Public display name. Folded into the same text pool.

    Returns:
        True  — at least one positive signal and no negative signals
                outweighing them.
        False — negative signals dominate (non-Latin script, non-NA flag).
        None  — no signal either way; the caller picks the default policy.
    """
    text = " ".join(part for part in (bio, display_name) if part)
    if not text.strip():
        return None

    pos = _positive_score(text)
    neg = _negative_score(text)

    if pos == 0 and neg == 0:
        return None
    if neg > pos:
        return False
    if pos > 0:
        return True
    return None
