"""Tests for medical_claims_validator.py — banned medical/credential claim
scanner + hard-fail gate for wp-post-creator blog drafts. Pure, no network."""
# ruff: noqa: S101

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import pytest

from lib import medical_claims_validator as mcv

# ── Known-bad claims: one per category ──────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected_term"),
    [
        ("As verified by our veterinary team, this diet is ideal.", "veterinarian_credential"),
        ("As a dog nutrition expert, I recommend this food.", "nutritionist_credential"),
        ("This post was medically reviewed before publishing.", "medical_professional_credential"),
        ("Here is our medical advice for your dog's joints.", "medical_advice_claim"),
        ("This recipe cures joint pain in senior dogs.", "cure_claim"),
        ("This supplement treats arthritis in large breeds.", "treatment_claim"),
        ("Our quiz diagnoses your dog's allergy in minutes.", "diagnosis_claim"),
        ("Follow the recommended dosage printed on the label.", "dosage_claim"),
        ("Our prescription strength chew fixes joint support.", "prescription_claim"),
        ("This food is completely safe for all dogs, guaranteed.", "absolute_efficacy_claim"),
    ],
)
def test_known_bad_claims_flagged(text: str, expected_term: str) -> None:
    hits = mcv.find_banned_claims(text)
    assert expected_term in hits


# ── Negation cases: must NOT false-positive ─────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "We are not veterinarians, nutritionists, or medical professionals.",
        "This isn't medical advice — always ask your own vet.",
        "This homemade treat doesn't cure anything; it's just a treat.",
        "The author is not a licensed veterinarian.",
        "This guide should not be considered official medical guidance.",
        "This chew is not prescribed medication — it's a regular treat.",
        "As always, consult your vet before starting a new diet.",
        "Ask your veterinarian if this recipe fits your dog's needs.",
    ],
)
def test_negated_or_benign_claims_not_flagged(text: str) -> None:
    assert mcv.find_banned_claims(text) == []


@pytest.mark.parametrize(
    "text",
    [
        # Live-reproduced false positive: a generic, third-party reference
        # to "vet-approved"/"veterinary-grade" is not a brand credential
        # claim and must not be flagged (see CREDENTIAL_CLAIM_TERMS comment).
        "A better approach is to work with a formula -- either a vet-approved "
        "recipe or a nutrient calculator -- that tells you exactly how much "
        "of each source to use.",
        "Look for a vet approved supplement at your local pet store.",
        "This is a veterinary-grade formula sold at clinics nationwide.",
        "Ask about veterinary grade options next time you visit.",
    ],
)
def test_generic_vet_approved_mentions_not_flagged(text: str) -> None:
    assert mcv.find_banned_claims(text) == []


def test_self_referential_vet_credential_claims_still_flagged() -> None:
    # The removal of bare "vet-approved" must not weaken genuine
    # self-credentialing claims -- those are still caught via the
    # already-covered self-referential phrasings.
    hits = mcv.find_banned_claims("Our veterinary team designed this recipe.")
    assert "veterinarian_credential" in hits


@pytest.mark.parametrize(
    "text",
    [
        # Live-reproduced false positive (idea c145740d, 2026-08-14, "The
        # 7-Day Elimination Diet Trial"): bare "prescribed" fired on prose
        # deferring to a REAL vet, and on an Amazon product title injected by
        # the affiliate resolver. Neither is the brand directing medication,
        # which is all DOSAGE_CLAIM_TERMS is meant to catch. The idea was
        # dropped to validation_failed with no reviewer and no stored reason.
        "When our vet prescribed a hydrolyzed protein dry food for Nalla, "
        "I thought I could handle it.",
        # Hyphen counts as a word boundary in _build_pattern, so this matched
        # the bare term too.
        "Blue Buffalo Natural Veterinary Diet HF Hydrolyzed — "
        "VETERINARY-PRESCRIBED FORMULA: digestible hydrolyzed (Pack of 24)",
        "These are usually prescription diets, like Royal Canin Hydrolyzed.",
        "For a week, Nalla got only her prescription kibble and water.",
        # "prescription strength"/"prescription grade" got the same possessive
        # treatment pre-emptively: bare adjective phrases with no subject fire
        # on third-party products and on affiliate-injected vendor titles.
        "Hill's Prescription Diet is a prescription-grade formula from your vet.",
        "PRESCRIPTION STRENGTH JOINT FORMULA for dogs (Pack of 2)",
        "Some clinics stock a prescription grade version of this chew.",
    ],
)
def test_third_party_prescription_mentions_not_flagged(text: str) -> None:
    assert mcv.find_banned_claims(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "We prescribed a hydrolyzed diet for your dog.",
        "We've prescribed this rotation for years.",
        "I prescribe a 20 mg chew for dogs her size.",
        "I prescribed a new kibble after the trial.",
        "We prescribe this for every itchy dog we meet.",
        "Our prescription strength chew is the one we reach for.",
        "Try our prescription-strength joint blend.",
        "Ask for our prescription grade formula.",
        "Ask for our prescription-grade formula.",
    ],
)
def test_first_person_prescription_claims_still_flagged(text: str) -> None:
    # Dropping bare "prescribed" must not weaken the actual banned case:
    # the brand itself directing medication.
    assert "prescription_claim" in mcv.find_banned_claims(text)


def test_clean_blog_paragraph_has_no_flags() -> None:
    text = (
        "Nalla turned her nose up at kibble for weeks, so we tried a slow "
        "cooker chicken-and-rice mix. She finished the bowl in under two "
        "minutes and asked for seconds with her whole back half wagging."
    )
    assert mcv.find_banned_claims(text) == []


def test_deduped_sorted_output_for_repeated_hits() -> None:
    text = "Cures, cures, and more cures — this cure works every time, guaranteed."
    hits = mcv.find_banned_claims(text)
    assert hits == sorted(set(hits))
    assert "cure_claim" in hits
    assert "absolute_efficacy_claim" in hits


# ── validate_blog_post: hard-fail gate ──────────────────────────────────────


def test_validate_blog_post_raises_on_violation() -> None:
    with pytest.raises(ValueError, match="banned medical/credential claim"):
        mcv.validate_blog_post("This recipe cures joint pain.", title="Great Dog Food")


def test_validate_blog_post_passes_clean_content() -> None:
    mcv.validate_blog_post(
        "We tried a new recipe with Nalla this week and she loved it.",
        title="Nalla's New Favorite Dinner",
    )  # must not raise


def test_validate_blog_post_scans_title_too() -> None:
    with pytest.raises(ValueError, match="banned medical/credential claim"):
        mcv.validate_blog_post(
            "Totally ordinary body text.", title="As a veterinarian, I approve this"
        )


# ── Draft-creation path: must abort (never POST) on a violation ────────────
#
# wp-post-creator has no committed Python call site (it's an interactive
# SKILL.md flow — see SKILL.md Step 3.6 -> Step 4). This helper mirrors that
# documented validate-then-POST sequence so the GATE ORDERING is testable
# without inventing an unused production function.


def _create_wp_draft_documented_flow(
    title: str, content: str, wp_client_factory: Callable[[], Any]
) -> dict[str, Any]:
    mcv.validate_blog_post(content, title=title)
    with wp_client_factory() as client:
        result: dict[str, Any] = client.post(
            "/wp-json/wp/v2/posts", json={"title": title, "content": content}
        ).json()
        return result


def test_draft_creation_aborts_on_violation_without_posting() -> None:
    fake_client = MagicMock()
    fake_factory = MagicMock()
    fake_factory.return_value.__enter__.return_value = fake_client

    with pytest.raises(ValueError, match="banned medical/credential claim"):
        _create_wp_draft_documented_flow(
            "5 Tips For Dogs",
            "As a veterinarian, I recommend this food for joint pain.",
            fake_factory,
        )

    fake_client.post.assert_not_called()


def test_draft_creation_posts_when_content_is_clean() -> None:
    fake_client = MagicMock()
    fake_client.post.return_value.json.return_value = {"id": 1, "status": "draft"}
    fake_factory = MagicMock()
    fake_factory.return_value.__enter__.return_value = fake_client

    result = _create_wp_draft_documented_flow(
        "5 Tips For Dogs",
        "We tried a new recipe with Nalla this week and she loved it.",
        fake_factory,
    )

    fake_client.post.assert_called_once()
    assert result == {"id": 1, "status": "draft"}
