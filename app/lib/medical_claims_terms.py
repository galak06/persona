"""Banned medical/credential claim vocabulary, grouped by claim category.

Pure data for `lib.medical_claims_validator`, split out of it so the term
lists (which grow every time a live false positive is triaged) and the
matching logic stay independently readable and under the 300-line cap.
The validator re-exports every name here, so existing
`from lib.medical_claims_validator import CLAIM_CATEGORIES` imports keep
working unchanged.

SELF-REFERENCE RULE (the single design constraint for every list below):
only phrases where *the brand* speaks as, or for, a credentialed
professional are banned. A bare profession/action word that fires no
matter who is being described is a bug, not a stricter gate — this brand's
own voice guide *recommends* deferring to a real vet, so bare terms punish
exactly the language the brand is supposed to use. Two live regressions
have now been caused by violating this rule (see `vet-approved` and
`prescribed` notes below); check new terms against it before adding them.
"""

from __future__ import annotations

# ── Term lists, grouped by claim category ──────────────────────────────────

# Implied professional credentials this brand does not hold. Self-referential
# phrasing only (see module docstring) — recommending a REAL vet is fine.
# "veterinary-grade"/"veterinary grade"/"vet-approved"/"vet approved" were
# removed from here: they're bare, non-self-referential phrases that fire on
# any mention regardless of who's being described, violating this section's
# own stated intent. Live-reproduced false positive: a draft describing a
# generic third-party "vet-approved recipe" (not a brand credential claim at
# all) was rejected on this term, silently killing a `crewai_content_pipeline`
# idea with no human ever reviewing why (see git history on this comment for
# the exact idea/date if needed).
CREDENTIAL_CLAIM_TERMS: dict[str, tuple[str, ...]] = {
    "veterinarian_credential": (
        "as a veterinarian",
        "as your veterinarian",
        "as a licensed veterinarian",
        "as a board-certified veterinarian",
        "we are veterinarians",
        "we're veterinarians",
        "our veterinarian",
        "our veterinarians",
        "our vet team",
        "our veterinary team",
        "our in-house veterinarian",
        "our staff veterinarian",
        "licensed veterinarian",
        "board-certified veterinarian",
        "practicing veterinarian",
        "veterinarian on staff",
    ),
    "nutritionist_credential": (
        "as a nutritionist",
        "as a dog nutritionist",
        "as a canine nutritionist",
        "as a certified nutritionist",
        "as a nutrition expert",
        "as a dog nutrition expert",
        "we are nutritionists",
        "our nutritionist",
        "our nutritionists",
        "certified canine nutritionist",
        "certified dog nutritionist",
        "certified nutritionist",
        "board-certified nutritionist",
    ),
    "medical_professional_credential": (
        "as a doctor",
        "as a physician",
        "our medical team",
        "our medical staff",
        "our doctors",
        "medically reviewed",
        "medically approved",
        "clinically reviewed",
        "clinically approved",
        "reviewed by our veterinarian",
        "reviewed by our doctors",
        "doctor of veterinary medicine",
    ),
    "medical_advice_claim": (
        "medical advice",
        "veterinary advice",
        "professional medical advice",
        "official medical guidance",
    ),
}

# Disease cure / treatment / diagnosis assertions.
DISEASE_CLAIM_TERMS: dict[str, tuple[str, ...]] = {
    "cure_claim": (
        "cure",
        "cures",
        "cured",
        "curing",
        "cure for",
        "cure for cancer",
        "will cure",
        "guaranteed to cure",
        "guaranteed cure",
        "proven to cure",
        "miracle cure",
        "natural cure",
        "reverses cancer",
        "reverses diabetes",
        "reverses the disease",
        "eliminates the disease",
        "eliminates cancer",
    ),
    "treatment_claim": (
        "treats disease",
        "treats cancer",
        "treats arthritis",
        "treats diabetes",
        "treatment for cancer",
        "treatment for arthritis",
        "treats this condition",
        "treats the condition",
    ),
    "diagnosis_claim": (
        "diagnoses",
        "diagnose your dog",
        "diagnosed with",
        "this will diagnose",
    ),
}

# Dosage / prescription language — implies the brand is directing medication.
#
# Bare "prescribed" was removed here for the same reason "vet-approved" was
# removed from CREDENTIAL_CLAIM_TERMS above: it is not self-referential, so it
# fired on anyone prescribing anything. Live-reproduced (idea
# c145740d, 2026-08-14, "The 7-Day Elimination Diet Trial"), where it hit
# twice, neither a brand claim:
#   1. "When our vet prescribed a hydrolyzed protein dry food for Nalla" —
#      persona prose deferring to a REAL vet, i.e. exactly the language the
#      voice guide asks for.
#   2. "VETERINARY-PRESCRIBED FORMULA" — inside an Amazon product title
#      injected by the affiliate resolver; third-party marketing copy the
#      brand never wrote.
# Note `_build_pattern` bounds on `[a-z0-9]`, so a hyphen counts as a word
# boundary and "veterinary-prescribed" matched the bare term. The replacement
# variants are first-person only, mirroring the existing "we prescribe".
#
# "prescription strength"/"prescription grade" were given the same possessive
# treatment for the same reason, before they could cause a third regression:
# both are bare adjective phrases with no subject, so they fired on any
# third-party product legitimately described that way ("Hill's Prescription
# Diet is a prescription-grade formula from your vet") and on vendor titles
# the affiliate resolver injects verbatim. The banned claim is the BRAND
# asserting its own pick has pharmaceutical potency, which needs a possessive.
#
# Deliberate coverage tradeoff: a subject-less "this chew is prescription
# strength" no longer flags. That reads as a brand assertion to a human, but
# there is no way to separate it from vendor copy on phrase matching alone,
# and on the crewai path a false positive silently discards a whole draft
# (no reviewer, no stored reason) while a false negative still faces the
# quality editor. Revisit only with real attribution parsing, not more terms.
DOSAGE_CLAIM_TERMS: dict[str, tuple[str, ...]] = {
    "dosage_claim": (
        "recommended dosage",
        "safe dosage",
        "correct dosage",
        "proper dosage",
        "dosing chart",
        "dosing schedule",
        "mg per pound",
        "mg per kg",
        "mg/kg",
    ),
    "prescription_claim": (
        "we prescribe",
        "we prescribed",
        "we've prescribed",
        "i prescribe",
        "i prescribed",
        "our prescription strength",
        "our prescription-strength",
        "our prescription grade",
        "our prescription-grade",
    ),
}

# Absolute-health-claim language — extends the social-comment "never make
# absolute health claims" rule (app/CLAUDE.md Universal DON'T) to blog posts.
ABSOLUTE_HEALTH_CLAIM_TERMS: dict[str, tuple[str, ...]] = {
    "absolute_efficacy_claim": (
        "guaranteed to work",
        "guaranteed results",
        "100% effective",
        "completely safe for all dogs",
        "totally safe for all dogs",
        "risk-free",
        "no side effects",
        "always works",
        "never fails",
        "works every time",
    ),
}

ALL_CLAIM_TERMS: dict[str, tuple[str, ...]] = {
    **CREDENTIAL_CLAIM_TERMS,
    **DISEASE_CLAIM_TERMS,
    **DOSAGE_CLAIM_TERMS,
    **ABSOLUTE_HEALTH_CLAIM_TERMS,
}

CLAIM_CATEGORIES: dict[str, str] = {
    canonical: category
    for category, terms in (
        ("implied_credential", CREDENTIAL_CLAIM_TERMS),
        ("disease_cure_or_treatment", DISEASE_CLAIM_TERMS),
        ("dosage_or_prescription", DOSAGE_CLAIM_TERMS),
        ("absolute_health_claim", ABSOLUTE_HEALTH_CLAIM_TERMS),
    )
    for canonical in terms
}
