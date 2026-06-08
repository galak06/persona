"""Dog-safety ingredient scanner.

Pure, I/O-free functions that flag canine-toxic ingredients in recipe
ingredient lists. The hard gate (`dog_safe`) is driven by `TOXIC_TERMS`;
soft, dose-dependent concerns (excess salt / high fat) are kept SEPARATE in
`SOFT_WARNING_TERMS` so they never silently fail the safety gate.

Matching strategy
-----------------
We match each variant against the lowercased ingredient text using a
WORD-BOUNDARY regex rather than a naive substring test. This keeps "garlic"
matching "garlic powder" / "roasted garlic" while avoiding false positives
where one toxic token is a substring of an innocuous word (e.g. the variant
"tea" must not fire on "steak" or "team"; "currant" must not fire inside an
unrelated longer word). Multi-word variants (e.g. "onion powder", "birch
sugar") are matched as phrases with boundaries on each end.

Tradeoffs
---------
Word-boundary matching can still over-match in rare compound cases (e.g.
"chive" would match a hypothetical "garlic-chive" blend, which is correct
here). We bias toward FALSE POSITIVES over false negatives: for dog safety,
flagging a borderline ingredient is the safe failure mode. Reviewers can set
`RecipeRow.override = True` downstream to clear an intentional flag.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import only for type checking
    from recipe_db.models import Ingredient


# Canonical toxic ingredient -> tuple of lowercase variants/synonyms.
# Each variant is matched case-insensitively with word boundaries.
TOXIC_TERMS: dict[str, tuple[str, ...]] = {
    # Allium family — toxic raw, cooked, or powdered (cumulative dose).
    "onion": (
        "onion",
        "onions",
        "onion powder",
        "dried onion",
        "green onion",
        "spring onion",
        "shallot",
        "shallots",
        "scallion",
        "scallions",
    ),
    "garlic": (
        "garlic",
        "garlic powder",
        "garlic clove",
        "garlic cloves",
        "granulated garlic",
        "minced garlic",
        "roasted garlic",
    ),
    "leek": ("leek", "leeks"),
    "chive": ("chive", "chives"),
    # Methylxanthines.
    "chocolate": (
        "chocolate",
        "dark chocolate",
        "milk chocolate",
        "semisweet chocolate",
        "chocolate chips",
        "chocolate chip",
        "chocolate bar",
    ),
    "cocoa": ("cocoa", "cocoa powder", "cocoa nibs"),
    "cacao": ("cacao", "cacao powder", "cacao nibs"),
    # Sweeteners.
    "xylitol": ("xylitol", "birch sugar", "birch sweetener"),
    # Grapes & dried grapes (note: only the GRAPE kind of currant).
    "grape": ("grape", "grapes", "grape juice"),
    "raisin": ("raisin", "raisins"),
    "sultana": ("sultana", "sultanas"),
    # "currant" here means the dried-grape kind (Zante currant). Fresh
    # black/red currants are a different berry, but we flag conservatively.
    "currant": ("currant", "currants", "zante currant"),
    # Nuts.
    "macadamia": ("macadamia", "macadamias", "macadamia nut", "macadamia nuts"),
    "walnut": ("walnut", "walnuts", "black walnut", "black walnuts"),
    # Alcohol / ethanol.
    "alcohol": (
        "alcohol",
        "ethanol",
        "beer",
        "wine",
        "liquor",
        "liqueur",
        "rum",
        "whiskey",
        "whisky",
        "vodka",
        "brandy",
        "bourbon",
    ),
    # Caffeine sources.
    "caffeine": ("caffeine",),
    "coffee": ("coffee", "coffee grounds", "instant coffee"),
    "espresso": ("espresso", "espresso powder"),
    # Caffeinated tea (herbal/decaf is safer, but flag conservatively).
    "tea": ("tea", "black tea", "green tea", "matcha"),
    # Other.
    "nutmeg": ("nutmeg",),
    "hops": ("hops",),
    "yeast dough": ("raw yeast dough", "yeast dough", "raw dough"),
    # Persin — avocado flesh is low-risk for dogs but pit/skin/leaves are
    # toxic; flag and let the safety note explain the persin nuance.
    "avocado": ("avocado", "avocados", "guacamole"),
    "mustard seed": ("mustard seed", "mustard seeds"),
}


# Soft, dose-dependent warnings. These DO NOT affect `dog_safe`.
SOFT_WARNING_TERMS: dict[str, tuple[str, ...]] = {
    "high salt": (
        "salt",
        "table salt",
        "sea salt",
        "kosher salt",
        "soy sauce",
        "bouillon",
        "stock cube",
        "broth",
    ),
    "high fat": (
        "butter",
        "lard",
        "bacon grease",
        "bacon fat",
        "heavy cream",
        "shortening",
    ),
}


def _build_pattern(variants: tuple[str, ...]) -> re.Pattern[str]:
    """Compile a single case-insensitive word-boundary regex for variants.

    Variants are sorted longest-first so multi-word phrases are preferred and
    escaped to treat any regex metacharacters literally.
    """
    ordered = sorted(variants, key=len, reverse=True)
    alternation = "|".join(re.escape(v) for v in ordered)
    return re.compile(rf"(?<![a-z0-9]){alternation}(?![a-z0-9])", re.IGNORECASE)


# Pre-compiled patterns keyed by canonical term (built once at import).
_TOXIC_PATTERNS: dict[str, re.Pattern[str]] = {
    canonical: _build_pattern(variants) for canonical, variants in TOXIC_TERMS.items()
}
_SOFT_PATTERNS: dict[str, re.Pattern[str]] = {
    canonical: _build_pattern(variants)
    for canonical, variants in SOFT_WARNING_TERMS.items()
}


def _match_terms(text: str, patterns: dict[str, re.Pattern[str]]) -> set[str]:
    """Return the set of canonical terms whose pattern matches `text`."""
    lowered = text.lower()
    return {canonical for canonical, pat in patterns.items() if pat.search(lowered)}


def scan_ingredient_lines(lines: list[str]) -> tuple[list[str], bool]:
    """Scan plain ingredient strings for canine-toxic terms.

    Standalone path with no dependency on the `Ingredient` dataclass.

    Returns
    -------
    (toxic_flags, dog_safe)
        `toxic_flags` is the sorted, deduped list of canonical toxic terms
        found; `dog_safe` is True iff no toxic terms were found.
    """
    found: set[str] = set()
    for line in lines:
        found |= _match_terms(line, _TOXIC_PATTERNS)
    flags = sorted(found)
    return flags, len(flags) == 0


def scan_ingredients(ingredients: list[Ingredient]) -> tuple[list[str], bool]:
    """Scan `Ingredient` objects (item + notes) for canine-toxic terms.

    Returns the same `(toxic_flags, dog_safe)` shape as
    `scan_ingredient_lines`.
    """
    lines = [f"{ing.item} {ing.notes}" for ing in ingredients]
    return scan_ingredient_lines(lines)


def soft_warnings(ingredients: list[Ingredient]) -> list[str]:
    """Return sorted soft (dose-dependent) warnings for `Ingredient` objects.

    These are advisory only and are intentionally NOT part of the `dog_safe`
    gate (a pinch of salt is fine; a salt-cured ingredient is not — judgment
    call left to the reviewer).
    """
    lines = [f"{ing.item} {ing.notes}" for ing in ingredients]
    return soft_warning_lines(lines)


def soft_warning_lines(lines: list[str]) -> list[str]:
    """Soft-warning scan over plain strings (string-only convenience path)."""
    found: set[str] = set()
    for line in lines:
        found |= _match_terms(line, _SOFT_PATTERNS)
    return sorted(found)


def safety_note(toxic_flags: list[str]) -> str:
    """Build a human-readable dog-safety note for the seed export.

    If `toxic_flags` is empty, returns a generic positive note. Otherwise
    returns an explicit warning naming each flagged ingredient. Suitable for
    the seed's `dog_safety_notes` field.
    """
    if not toxic_flags:
        return (
            "Dog-safe: no known canine-toxic ingredients detected. As always, "
            "introduce new foods gradually and consult your vet for dogs with "
            "allergies or health conditions."
        )
    listed = ", ".join(sorted(dict.fromkeys(toxic_flags)))
    return (
        "WARNING — NOT dog-safe as written. Contains ingredient(s) toxic or "
        f"unsafe for dogs: {listed}. Do not feed to dogs without removing or "
        "substituting these ingredients."
    )
