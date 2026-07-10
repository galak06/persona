"""Default keyword lists for `comment_generator.py::score_relevance`.

Holds the literal keyword lists previously hardcoded inline in
`score_relevance()`. A brand's `config.json` under
`content_analysis.keywords.{primary_keywords,secondary_keywords,
competitor_mentions}` is now the source of truth for scoring — these
constants are read ONLY as a fallback when a brand's config omits a key
entirely (never when the key is present-but-empty, which is a deliberate
"no bonus yet" state for a freshly onboarded brand).

Verbatim relocation of the values dogfoodandfun's engagement scanners have
always used. Kept in a separate module so `comment_generator.py` stays
readable — same rationale as
`lib/engagement/adapters/instagram_dom.py` keeping its JS payloads and
account constants out of the main adapter file.
"""

from __future__ import annotations

# Food / nutrition signals (broadened — these groups ARE about dog food).
# Fallback for content_analysis.keywords.primary_keywords when the key is
# missing from a brand's config.json.
DEFAULT_PRIMARY_KEYWORDS: list[str] = [
    "dog food",
    "homemade",
    "recipe",
    "nutrition",
    "ingredients",
    "raw",
    "kibble",
    "diet",
    "feeding",
    "meal",
    "protein",
    "grain",
    "food",
    "treat",
    "snack",
    "chew",
    "supplement",
    "vitamin",
    "probiotic",
    "omega",
    "calcium",
    "freeze dried",
    "dehydrated",
    "batch cook",
    "prep",
    "topper",
    "fresh pet",
    "freshpet",
    "transition",
    "switching",
    "picky eater",
    "allergy",
    "sensitive",
    "stomach",
    "digestive",
    "gut",
    "dental",
    "teeth",
    "yogurt",
    "pumpkin",
    "sardine",
    "chicken",
    "beef",
    "turkey",
    "salmon",
    "sweet potato",
    "broth",
    "bone broth",
    "coconut oil",
]

# GPS / running / active dog signals.
# Fallback for content_analysis.keywords.secondary_keywords when the key is
# missing from a brand's config.json.
DEFAULT_SECONDARY_KEYWORDS: list[str] = [
    "gps",
    "tracker",
    "running",
    "canicross",
    "trail",
    "hike",
    "gear",
    "collar",
    "leash",
    "activity",
    "exercise",
    "sport",
    "fi ",
    "tractive",
    "walk",
    "adventure",
]

# Specific brands reviewed on site.
# Fallback for content_analysis.keywords.competitor_mentions when the key
# is missing from a brand's config.json.
DEFAULT_COMPETITOR_MENTIONS: list[str] = [
    "fi collar",
    "tractive",
    "whistle",
    "link akc",
    "ollie",
    "nom nom",
    "the farmer's dog",
    "open farm",
]
