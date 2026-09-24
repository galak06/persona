"""Country detection in free group text, for fb-group-scout's geo filter.

Engine-generic: a small gazetteer of country names, a few well-known
cities/regions per country, and ``detect_countries`` over normalized text.
Which countries a brand *wants* is brand data (``ScoutRules``), never here.

Demonyms that double as dog-breed names ("german", "french", "australian",
"spanish", "english", "italian", "irish", "scottish") are deliberately absent:
"German Shepherd owners USA" must not read as a German group.
"""

from __future__ import annotations

import re

# ISO-3166 alpha-2 (plus "EU" for the bare region) -> aliases.
GAZETTEER: dict[str, tuple[str, ...]] = {
    "US": (
        "usa",
        "u s a",
        "united states",
        "america",
        "north america",
        "new york",
        "california",
        "texas",
        "florida",
        "chicago",
        "los angeles",
        "seattle",
        "boston",
        "atlanta",
        "denver",
        "houston",
        "dallas",
        "new mexico",
        "new england",
        "new jersey",
        "new orleans",
    ),
    "CA": (
        "canada",
        "canadian",
        "toronto",
        "vancouver",
        "montreal",
        "calgary",
        "ottawa",
        "edmonton",
        "winnipeg",
        "ontario",
        "quebec",
        "alberta",
        "british columbia",
        "nova scotia",
        "manitoba",
        "saskatchewan",
    ),
    "GB": (
        "uk",
        "u k",
        "united kingdom",
        "britain",
        "great britain",
        "england",
        "scotland",
        "wales",
        "northern ireland",
        "london",
        "manchester",
        "liverpool",
        "leeds",
        "glasgow",
        "edinburgh",
        "cardiff",
        "yorkshire",
        "cornwall",
    ),
    "IE": ("ireland", "dublin"),
    "AU": ("australia", "sydney", "melbourne", "brisbane", "adelaide", "queensland"),
    "NZ": ("new zealand", "nz", "auckland"),
    "PH": (
        "philippines",
        "pilipinas",
        "filipino",
        "pilipino",
        "pinoy",
        "manila",
        "cebu",
        "davao",
        "bacolod",
        "iloilo",
        "quezon city",
        "bentahan",
    ),
    "IN": ("india", "delhi", "mumbai", "bangalore", "kolkata", "chennai"),
    "PK": ("pakistan", "lahore", "karachi"),
    "BD": ("bangladesh", "dhaka"),
    "LK": ("sri lanka",),
    "ID": ("indonesia", "jakarta"),
    "MY": ("malaysia", "kuala lumpur"),
    "SG": ("singapore",),
    "VN": ("vietnam",),
    "TH": ("thailand", "bangkok"),
    "NG": ("nigeria", "lagos"),
    "KE": ("kenya", "nairobi"),
    "ZA": ("south africa", "johannesburg", "cape town"),
    "GH": ("ghana",),
    "EG": ("egypt",),
    "IL": ("israel",),
    "SA": ("saudi",),
    "AE": ("uae", "dubai", "abu dhabi"),
    "ES": (
        "spain",
        "españa",
        "espana",
        "madrid",
        "barcelona",
        "sevilla",
        "seville",
        "galicia",
        "pontevedra",
        "andalucia",
    ),
    "PT": ("portugal", "lisbon", "lisboa"),
    "FR": ("france",),
    "DE": ("germany", "deutschland", "berlin", "munich"),
    "IT": ("italy", "italia", "rome", "milan"),
    "NL": ("netherlands", "amsterdam"),
    "PL": ("poland",),
    "MX": ("mexico", "méxico"),
    "EU": ("europe", "european union"),
}


def normalize(text: str) -> str:
    """Lowercase, collapse punctuation to single spaces, pad with spaces so
    whole-phrase matching is a plain `f" {phrase} " in text` check."""
    return " " + " ".join(re.sub(r"[^\w']+", " ", text.lower()).split()) + " "


def contains_phrase(normalized_text: str, phrase: str) -> bool:
    """Whole-word/phrase match of ``phrase`` inside already-normalized text."""
    needle = normalize(phrase)
    return needle.strip() != "" and needle in normalized_text


def _strip(normalized_text: str, phrases: list[str]) -> str:
    for phrase in sorted(phrases, key=len, reverse=True):
        needle = normalize(phrase)
        while needle in normalized_text:
            normalized_text = normalized_text.replace(needle, " ", 1)
    return normalized_text


def detect_countries(text: str, ignore: frozenset[str] = frozenset()) -> set[str]:
    """Return the codes of countries NOT in ``ignore`` named in ``text``.

    Aliases of the ``ignore`` countries are stripped first (longest first), so
    an allowed "New Mexico" or "British Columbia" can't leak a false "MX"
    or "GB" hit from its shorter substring.
    """
    ignored = [a for code in ignore for a in GAZETTEER.get(code, ())]
    norm = _strip(normalize(text), ignored)
    return {
        code
        for code, aliases in GAZETTEER.items()
        if code not in ignore and any(contains_phrase(norm, a) for a in aliases)
    }


def parse_countries(target_market: str) -> frozenset[str]:
    """Country codes named in a free-text target market ("USA + Canada")."""
    return frozenset(detect_countries(target_market))
