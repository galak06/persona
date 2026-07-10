"""Season domain logic for the pipeline's seasonal-selection phase.

Pure functions, no I/O. The target market is USA + Canada (Northern
hemisphere), so the calendar-to-season mapping is by month. A recipe with no
inferred seasons is treated as *all-season* (eligible year-round).
"""

from __future__ import annotations

from datetime import date

SPRING = "spring"
SUMMER = "summer"
FALL = "fall"
WINTER = "winter"

# Canonical ordering used by every consumer (inference output, UI dropdown).
SEASONS: tuple[str, ...] = (SPRING, SUMMER, FALL, WINTER)

# Month -> season (Northern hemisphere). Dec/Jan/Feb = winter, etc.
_MONTH_SEASON: dict[int, str] = {
    12: WINTER, 1: WINTER, 2: WINTER,
    3: SPRING, 4: SPRING, 5: SPRING,
    6: SUMMER, 7: SUMMER, 8: SUMMER,
    9: FALL, 10: FALL, 11: FALL,
}

# Seasonal signal keywords -> season. Lowercase; matched as substrings against
# a recipe's title + tags + category. Curated for dog-treat recipes.
_SEASON_KEYWORDS: dict[str, frozenset[str]] = {
    FALL: frozenset({
        "pumpkin", "apple", "cinnamon", "sweet potato", "cranberry",
        "harvest", "thanksgiving", "autumn", "fall", "maple", "pecan",
    }),
    WINTER: frozenset({
        "peppermint", "gingerbread", "ginger", "eggnog", "holiday",
        "christmas", "hanukkah", "winter", "candy cane", "nutmeg",
    }),
    SPRING: frozenset({
        "strawberry", "carrot", "easter", "spring", "rhubarb",
        "lemon", "honey",
    }),
    SUMMER: frozenset({
        "frozen", "popsicle", "pupsicle", "watermelon", "berry",
        "blueberry", "bbq", "cooling", "refreshing", "summer", "coconut",
        "yogurt",
    }),
}


def current_season(today: date | None = None) -> str:
    """Return the season for ``today`` (defaults to the system date)."""
    day = today or date.today()
    return _MONTH_SEASON[day.month]


def normalize_season(value: str) -> str:
    """Lowercase/trim and validate a season string.

    Raises:
        ValueError: if ``value`` is not one of the four canonical seasons.
    """
    season = value.strip().lower()
    if season not in SEASONS:
        raise ValueError(
            f"unknown season {value!r}; expected one of {', '.join(SEASONS)}"
        )
    return season


def infer_seasons(title: str, tags: list[str], category: str = "") -> list[str]:
    """Infer the seasons a recipe suits from its text signal.

    Scans title + tags + category for curated seasonal keywords and returns the
    matching seasons in canonical order. An empty list means *all-season* (no
    strong seasonal signal) — such recipes are eligible year-round.
    """
    haystack = " ".join([title, " ".join(tags), category]).lower()
    return [
        season
        for season in SEASONS
        if any(keyword in haystack for keyword in _SEASON_KEYWORDS[season])
    ]


def in_season(recipe_seasons: list[str], target: str) -> bool:
    """True if a recipe with ``recipe_seasons`` is eligible in ``target``.

    All-season recipes (empty ``recipe_seasons``) are always eligible.
    """
    if not recipe_seasons:
        return True
    return target in recipe_seasons
