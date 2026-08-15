"""Banned medical/credential claim scanner for blog-post drafts.

Fail-closed safety net for `wp-post-creator`: a blog draft that implies
professional credentials the brand does not hold (vet, nutritionist,
doctor), asserts a disease cure/treatment, gives dosage/prescription
language, or makes an absolute health-efficacy claim must never reach a
WordPress draft. Pure, I/O-free — same shape as `recipe_db/safety.py`,
scaled up for free-form blog prose instead of a controlled ingredient
vocabulary.

Matching strategy: each canonical claim maps to a tuple of lowercase phrase
variants, matched with a WORD-BOUNDARY regex (not naive substring) so e.g.
"cure" doesn't fire inside "obscures". Credential phrases are deliberately
self-referential ("our veterinarian", "as a nutritionist") rather than bare
profession nouns — this brand's own voice guide *recommends* "consult your
vet" as safe disclaimer language (`recipe_db.safety.safety_note` already
emits it), so a bare "vet"/"veterinarian" trigger would flag routine,
desirable disclaimers. Only claims where the brand speaks *as* or *for* a
credentialed professional are banned.

Negation-awareness: for each match, we look at the words immediately
preceding it within the same clause (split on `. ! ? ; \\n` — NOT commas,
so a negation still covers a list: "not a vet, nutritionist, or doctor").
A negation cue ("not", "isn't", "never", "without", ...) in that short
(6-word) window drops the hit: "we are not veterinarians" / "this isn't
medical advice" must not trigger. Mirrors
`generators/recipe.py::_validate()`'s clause-scoped negation for
"xylitol-free" / "no garlic", broadened for natural prose.

Tradeoffs: biased toward FALSE POSITIVES over false negatives, same as the
recipe safety scanner — a human reviews every `wp-post-creator` draft in
this slice, so a false flag costs a re-read while a missed real claim
ships. E.g. "cure" also matches colloquial "the cure for a slow Monday".
That tradeoff assumes a human is actually looking: on the
`crewai_content_pipeline` path a hit sets `validation_failed` and the idea
is dropped with no reviewer and no persisted reason, so a false positive
there costs a whole discarded draft. Hence the self-reference rule in
`lib.medical_claims_terms` — keep bare, non-self-referential terms out.

The term vocabulary itself lives in `lib.medical_claims_terms` and is
re-exported below, so `from lib.medical_claims_validator import
CLAIM_CATEGORIES` (and friends) keeps working.
"""

from __future__ import annotations

import re

from lib.medical_claims_terms import (
    ABSOLUTE_HEALTH_CLAIM_TERMS,
    ALL_CLAIM_TERMS,
    CLAIM_CATEGORIES,
    CREDENTIAL_CLAIM_TERMS,
    DISEASE_CLAIM_TERMS,
    DOSAGE_CLAIM_TERMS,
)

__all__ = [
    "ABSOLUTE_HEALTH_CLAIM_TERMS",
    "ALL_CLAIM_TERMS",
    "CLAIM_CATEGORIES",
    "CREDENTIAL_CLAIM_TERMS",
    "DISEASE_CLAIM_TERMS",
    "DOSAGE_CLAIM_TERMS",
    "find_banned_claims",
    "validate_blog_post",
]


def _build_pattern(variants: tuple[str, ...]) -> re.Pattern[str]:
    """Compile a case-insensitive word-boundary regex for variants.

    Longest-first order prefers multi-word phrases; each variant is escaped
    for literal matching. The alternation MUST be wrapped in a non-capturing
    group: `|` has the lowest regex precedence, so a bare
    `(?<!X)a|b|c(?!X)` only boundary-protects `a` and `c` — middle variants
    get no protection at all (a real bug in the sibling
    `recipe_db/safety.py::_build_pattern`, harmless there only by luck of
    which ingredient variants land mid-alternation).
    """
    ordered = sorted(variants, key=len, reverse=True)
    alternation = "|".join(re.escape(v) for v in ordered)
    return re.compile(rf"(?<![a-z0-9])(?:{alternation})(?![a-z0-9])", re.IGNORECASE)


_CLAIM_PATTERNS: dict[str, re.Pattern[str]] = {
    canonical: _build_pattern(variants) for canonical, variants in ALL_CLAIM_TERMS.items()
}

# Negation cues — checked in a short window immediately before a match, scoped
# to the current clause. Deliberately excludes bare "n't" (only appears
# attached to a word already listed below).
_NEGATION_WORDS = (
    "not",
    "no",
    "never",
    "without",
    "nor",
    "isn't",
    "aren't",
    "wasn't",
    "weren't",
    "doesn't",
    "don't",
    "didn't",
    "won't",
    "wouldn't",
    "shouldn't",
    "couldn't",
    "can't",
    "cannot",
    "hasn't",
    "haven't",
    "hadn't",
)
_NEGATION_RE = re.compile(r"\b(?:" + "|".join(_NEGATION_WORDS) + r")\b")
_CLAUSE_DELIMITERS_RE = re.compile(r"[.!?;\n]")
_NEGATION_WINDOW_WORDS = 6


def _is_negated(lowered_text: str, match_start: int) -> bool:
    """True if a negation cue precedes the match within the same clause."""
    prefix = lowered_text[:match_start]
    clause_ends = [m.end() for m in _CLAUSE_DELIMITERS_RE.finditer(prefix)]
    clause_before = prefix[clause_ends[-1] if clause_ends else 0 :]
    window = " ".join(clause_before.split()[-_NEGATION_WINDOW_WORDS:])
    return bool(_NEGATION_RE.search(window))


def find_banned_claims(text: str) -> list[str]:
    """Scan `text` for banned medical/credential claim phrases.

    Returns the sorted, deduped list of canonical claim terms found, after
    dropping clause-locally-negated occurrences (see module docstring).
    Empty/falsy input returns an empty list.
    """
    if not text:
        return []
    lowered = text.lower()
    found: set[str] = set()
    for canonical, pattern in _CLAIM_PATTERNS.items():
        for match in pattern.finditer(lowered):
            if not _is_negated(lowered, match.start()):
                found.add(canonical)
                break
    return sorted(found)


def validate_blog_post(content: str, *, title: str = "") -> None:
    """Hard-fail gate: raise ValueError if a banned claim is present.

    Call this on the fully-assembled post (title + body) BEFORE the
    WordPress REST POST that creates the draft. Fail closed: on any hit,
    no draft is created. Mirrors `generators/recipe.py::_validate()`'s
    ValueError-on-hit contract so callers need no extra exception import.
    """
    hits = find_banned_claims(f"{title}\n{content}")
    if hits:
        detailed = [f"{term} ({CLAIM_CATEGORIES.get(term, 'uncategorized')})" for term in hits]
        raise ValueError(f"banned medical/credential claim(s) detected in blog post: {detailed}")
