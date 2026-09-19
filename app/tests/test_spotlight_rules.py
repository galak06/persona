"""Tests for `lib.crew.spotlight.rules` -- the blocking and flag-only checks.

Both functions are pure, so every case here is a hand-built `SocialPostPlan`:
no crew, no LLM, no brand directory. The plan below is the shape a compliant
spotlight actually takes, and each test breaks exactly one thing in it -- the
only way to prove a rule fires for its own reason rather than riding along on
someone else's violation.

The pairing that matters: every rule asserted here is stated in
`lib/crew/spotlight/prompts.py`. A rule with no prompt sentence is a rule the
writer cannot satisfy, and it would burn all three crew attempts.
"""
# ruff: noqa: S101

from __future__ import annotations

from lib.affiliate_resolver import ProductEntry
from lib.crew.socialpost.models import SocialPostPlan
from lib.crew.spotlight.prompts import DISCLOSURE_SENTENCE
from lib.crew.spotlight.rules import (
    FLAG_MASCOT_USAGE_UNVERIFIED,
    find_spotlight_flags,
    find_spotlight_violations,
)

_VERIFIED = ProductEntry(
    key="greenies-regular",
    asin="B0009YWQ2E",
    display="Greenies Regular Dental Dog Treats (36 ct)",
    category="dental-treats",
    notes="Verified against the VOHC accepted product list, checked 2026-03.",
)
_UNVERIFIED = ProductEntry(
    key="kong-classic",
    asin="B0002AR0I8",
    display="KONG Classic Dog Toy",
    category="enrichment",
    notes="Natural rubber, dishwasher safe.",
)
#: Neither a display name nor a usable key: `brand_token` returns "" and the
#: brand-token rules must go quiet rather than fail everything.
_NAMELESS = ProductEntry(key="", asin="B0000000", display="", category=None, notes=None)

_FB = (
    "Greenies Regular Dental Treats work on the gum line most brushing misses. "
    "Nalla is due for her yearly cleaning and this is the in-between plan. "
    "Want the full routine? Comment TEETH and I'll DM you the link.\n"
    "Follow the page for more dental posts.\n"
    f"{DISCLOSURE_SENTENCE}"
)
_IG = (
    "Greenies Regular Dental Treats work on the gum line most brushing misses. "
    "Want the full routine? Comment TEETH and I'll DM you the link.\n"
    "Follow for more dental posts.\n"
    f"{DISCLOSURE_SENTENCE}\n"
    "#dogdental #dogtreats #dogcare"
)


def _plan(**overrides: str) -> SocialPostPlan:
    fields: dict[str, str] = {
        "target_question": "Do dental treats actually clean a dog's teeth?",
        "comment_keyword": "TEETH",
        "fb_caption": _FB,
        "ig_caption": _IG,
        "overlay_headline": "THE GUM LINE",
        "overlay_subcopy": "What brushing misses",
        "image_brief": "A shepherd mix chewing on a living-room rug in warm afternoon light.",
        "cta_ribbon_text": "FULL GUIDE -> DOGFOODANDFUN.COM",
        "image_alt_text": "A dog chewing on a rug in the afternoon.",
    }
    fields.update(overrides)
    return SocialPostPlan(**fields)


def _violations(product: ProductEntry = _VERIFIED, **overrides: str) -> list[str]:
    return find_spotlight_violations(_plan(**overrides), product=product)


# ── baseline ──────────────────────────────────────────────────────────────


def test_a_compliant_spotlight_has_no_violations() -> None:
    assert _violations() == []


# ── rule 1: the disclosure ────────────────────────────────────────────────


def test_missing_disclosure_blocks_the_caption_that_is_missing_it() -> None:
    violations = _violations(fb_caption=_FB.replace(DISCLOSURE_SENTENCE, ""))
    assert len(violations) == 1
    assert violations[0].startswith("fb_caption must contain the affiliate disclosure")
    # The message is retry feedback, so it must carry the sentence to paste
    # AND where to paste it -- the base brief wants a question last.
    assert DISCLOSURE_SENTENCE in violations[0]
    assert "AFTER the closing question" in violations[0]


def test_missing_disclosure_in_both_captions_yields_two_violations() -> None:
    violations = _violations(
        fb_caption=_FB.replace(DISCLOSURE_SENTENCE, ""),
        ig_caption=_IG.replace(DISCLOSURE_SENTENCE, ""),
    )
    assert len(violations) == 2
    assert "BEFORE the hashtag line" in violations[1]


def test_a_lower_cased_disclosure_still_counts() -> None:
    """The marker, not the sentence: a model that changes the capitalisation
    should be corrected by the prompt, not failed by the rule."""
    lowered = _FB.replace(DISCLOSURE_SENTENCE, DISCLOSURE_SENTENCE.lower())
    assert _violations(fb_caption=lowered) == []


# ── rule 2: certifications ────────────────────────────────────────────────


def test_a_verified_product_may_name_its_own_certification() -> None:
    assert _violations(fb_caption=_FB.replace("work on", "are VOHC-accepted and work on")) == []


def test_an_unverified_product_may_not_name_a_certification() -> None:
    violations = _violations(
        _UNVERIFIED,
        fb_caption=_FB.replace(
            "Greenies Regular Dental Treats", "KONG Classic is VOHC-accepted. It"
        )
        + " KONG.",
        ig_caption=_IG.replace("Greenies Regular Dental Treats", "KONG Classic"),
    )
    assert any(
        v.startswith("fb_caption must not claim VOHC acceptance")
        and "no certification has been verified" in v
        for v in violations
    )


def test_a_verified_product_may_not_borrow_a_different_registry() -> None:
    """One registry was checked, not all of them."""
    violations = _violations(fb_caption=_FB.replace("work on", "are AAFCO approved and work on"))
    assert len(violations) == 1
    assert "must not claim AAFCO acceptance" in violations[0]
    assert "the only certification verified for this product is VOHC" in violations[0]


def test_a_negated_certification_passes() -> None:
    """A caption saying the product is NOT VOHC accepted is making the safe
    claim, not the risky one -- clause-local negation has to pass."""
    assert (
        _violations(
            _UNVERIFIED,
            fb_caption=_FB.replace(
                "Greenies Regular Dental Treats work",
                "KONG Classic toys are not VOHC accepted. They still work",
            ),
            ig_caption=_IG.replace("Greenies Regular Dental Treats", "KONG Classic"),
        )
        == []
    )


def test_overlay_text_is_checked_for_certifications_too() -> None:
    """The overlay is painted onto the image -- it is copy, not metadata."""
    violations = _violations(_UNVERIFIED, overlay_headline="AAFCO\nAPPROVED")
    assert any(v.startswith("overlay_headline must not claim AAFCO") for v in violations)


def _unverified_claim(sentence: str) -> list[str]:
    """Violations for a plan whose fb_caption OPENS with `sentence`.

    Everything else is made compliant for the unverified product -- both
    captions name KONG, both carry the disclosure -- so any violation returned
    is rule 2's and nothing else's.
    """
    return _violations(
        _UNVERIFIED,
        fb_caption=(
            f"{sentence} Want the full routine? Comment TEETH and I'll DM you the link.\n"
            f"{DISCLOSURE_SENTENCE}"
        ),
        ig_caption=_IG.replace("Greenies Regular Dental Treats", "KONG Classic"),
    )


def test_a_negation_in_another_clause_no_longer_suppresses_the_claim() -> None:
    """REGRESSION. The gate used to accept ANY cue in the previous six words,
    and neither a comma, a colon nor a dash ended a clause -- so a hook that
    merely opened on a negative word bought the sentence after it a free
    certification claim, silently, with no flag on the review card."""
    for sentence in (
        "No more plaque, KONG Classic is VOHC accepted.",
        "No brushing needed — KONG Classic is VOHC accepted.",
        "Debunking the myth: KONG Classic is VOHC accepted.",
        "Never guess again; no, KONG Classic is VOHC accepted.",
    ):
        violations = _unverified_claim(sentence)
        assert len(violations) == 1, sentence
        assert violations[0].startswith("fb_caption must not claim VOHC acceptance"), sentence


def test_a_negation_in_another_clause_of_the_overlay_is_caught_too() -> None:
    violations = _violations(_UNVERIFIED, overlay_subcopy="No plaque, VOHC accepted")
    assert any(v.startswith("overlay_subcopy must not claim VOHC") for v in violations)


def test_an_adjacent_negation_still_passes() -> None:
    """The guard against the opposite failure: the honest sentence this gate
    exists to keep sayable must not start burning crew retries. Includes the
    typographic apostrophe, which a model produces as readily as the ASCII
    one."""
    for sentence in (
        "KONG Classic is not VOHC accepted.",
        "KONG Classic is never VOHC accepted.",
        "KONG Classic isn’t VOHC accepted.",
        "KONG Classic toys are not yet VOHC accepted.",
    ):
        assert _unverified_claim(sentence) == [], sentence


def test_the_spelled_out_registry_name_is_a_claim_too() -> None:
    """REGRESSION. Matching only the acronym meant "Veterinary Oral Health
    Council accepted" -- which a reader understands perfectly -- evaded the
    gate completely. It is reported under the acronym so the message and the
    allow-list speak one vocabulary."""
    violations = _unverified_claim("KONG Classic is Veterinary Oral Health Council accepted.")
    assert len(violations) == 1
    assert violations[0].startswith("fb_caption must not claim VOHC acceptance")


def test_a_verified_product_may_spell_its_registry_out() -> None:
    assert (
        _violations(
            fb_caption=_FB.replace(
                "work on", "are Veterinary Oral Health Council accepted and work on"
            )
        )
        == []
    )


# ── rule 3: prices, discounts ─────────────────────────────────────────────


def test_a_price_in_a_caption_blocks() -> None:
    violations = _violations(fb_caption=f"{_FB}\nUnder $19.99 for the 36 count.")
    assert len(violations) == 1
    assert violations[0].startswith("fb_caption must not mention a price")


def test_a_written_currency_amount_blocks() -> None:
    assert _violations(ig_caption=f"{_IG}\nAbout 24 USD a bag.") != []


def test_a_percentage_discount_on_the_ribbon_blocks() -> None:
    violations = _violations(cta_ribbon_text="20% OFF TODAY")
    assert len(violations) == 1
    assert violations[0].startswith("cta_ribbon_text must not mention a price")


def test_deal_and_on_sale_block_in_the_overlay() -> None:
    assert _violations(overlay_subcopy="On sale this week") != []
    assert _violations(overlay_headline="TODAY'S\nDEAL") != []


def test_the_ways_of_quoting_a_price_that_used_to_pass_now_block() -> None:
    """REGRESSION. Rule 3's message already promised "a price, a discount, a
    deal or a sale" and CLAUDE.md says "Never state a price", but the regexes
    only knew `$12`, `12 USD`, `% off`, `deal` and `on sale`. Each line below
    reached a live affiliate caption."""
    for subcopy in (
        "Save 25% today",
        "Half price this week",
        "Big discount inside",
        "$  12 a bag",  # the currency gap was one optional space
        "Cheaper than a dental cleaning",
        "Sale ends Friday",
        "Priced for every household",
    ):
        violations = _violations(overlay_subcopy=subcopy)
        assert len(violations) == 1, subcopy
        assert violations[0].startswith("overlay_subcopy must not mention a price"), subcopy


def test_honest_copy_that_merely_contains_those_letters_still_passes() -> None:
    """The guard against the opposite failure: every one of these strings feeds
    a paid LLM retry when it misfires, so the widened rule 3 is whole-word on
    purpose -- "sale" must not fire inside "wholesale", nor "price" inside
    "priceless"."""
    assert (
        _violations(
            fb_caption=f"{_FB}\nIt saves you the guesswork between cleanings.",
            overlay_subcopy="Priceless peace of mind",
            cta_ribbon_text="WHOLESALE SIZES ASIDE -> DOGFOODANDFUN.COM",
        )
        == []
    )


# ── rule 4: the caption must name the product ─────────────────────────────


def test_a_caption_that_never_names_the_product_blocks() -> None:
    violations = _violations(
        ig_caption=_IG.replace("Greenies Regular Dental Treats", "These treats")
    )
    assert len(violations) == 1
    assert violations[0].startswith("ig_caption must name the product")
    # Retry feedback names both the full product and the one word that settles it.
    assert "Greenies Regular Dental Dog Treats" in violations[0]
    assert '"greenies"' in violations[0]


def test_a_product_with_no_usable_name_skips_the_brand_rules() -> None:
    """ "Cannot check" is not "absent" -- the alternative is failing every
    composition for a catalog entry nobody can even name."""
    assert _violations(_NAMELESS, image_brief="A dog on a rug.") == []


# ── rule 5: the image brief ───────────────────────────────────────────────


def test_packaging_words_in_the_image_brief_block() -> None:
    violations = _violations(image_brief="A dog beside the treat bag and its label, warm light.")
    assert len(violations) == 1
    assert violations[0].startswith("image_brief must describe the scene only")
    assert "'bag'" in violations[0] and "'label'" in violations[0]


def test_every_other_container_the_model_could_ask_for_blocks_too() -> None:
    """REGRESSION. The list stopped at `box`, so a brief asking for a pouch, a
    tub or a jar still handed the image model a surface to invent a label on.
    The prompt's own catch-all is "or any readable surface"."""
    violations = _violations(
        image_brief=(
            "A dog beside a pouch, a tub, a canister, a jar, a packet, a wrapper, "
            "a tin, a carton and a container on the rug."
        )
    )
    assert len(violations) == 1
    assert violations[0].startswith("image_brief must describe the scene only")
    for word in ("pouch", "tub", "canister", "jar", "packet", "wrapper", "tin", "carton"):
        assert f"'{word}'" in violations[0]


def test_words_that_merely_contain_a_packaging_noun_do_not_block() -> None:
    """The guard against the opposite failure: `tin` must not fire on "tiny",
    `jar` on "jarring", `box` on "boxwood", `packet` on "packed"."""
    assert (
        _violations(
            image_brief=(
                "A tiny shepherd mix waking with a jarring stretch beside a boxwood "
                "hedge, packed earth underfoot, warm afternoon light."
            )
        )
        == []
    )


def test_the_brand_name_in_the_image_brief_blocks() -> None:
    violations = _violations(image_brief="A dog chewing a Greenies on the rug in warm light.")
    assert len(violations) == 1
    assert violations[0].startswith("image_brief must not name the brand")


# ── flags (never blocking) ────────────────────────────────────────────────


def _flags(facts: str, *, mascot_name: str = "Nalla", **overrides: str) -> list[str]:
    return find_spotlight_flags(
        _plan(**overrides), product=_VERIFIED, mascot_name=mascot_name, mascot_facts=facts
    )


def test_unverified_mascot_usage_is_flagged() -> None:
    assert _flags(
        "Nalla is a shepherd mix, about 50 lb.",
        fb_caption=f"{_FB}\nNalla eats Greenies after dinner every night.",
    ) == [FLAG_MASCOT_USAGE_UNVERIFIED]


def test_mascot_usage_is_not_flagged_when_the_facts_file_confirms_it() -> None:
    assert (
        _flags(
            "Nalla is a shepherd mix and has had Greenies since she was two.",
            fb_caption=f"{_FB}\nNalla eats Greenies after dinner every night.",
        )
        == []
    )


def test_the_flag_fires_once_even_when_both_captions_claim_it() -> None:
    assert _flags(
        "Nalla is a shepherd mix.",
        fb_caption=f"{_FB}\nNalla loves Greenies.",
        ig_caption=f"{_IG}\nNalla chews her Greenies nightly.",
    ) == [FLAG_MASCOT_USAGE_UNVERIFIED]


def test_mascot_and_product_in_different_sentences_are_not_a_usage_claim() -> None:
    """Every spotlight mentions both somewhere. Only one sentence asserting
    the dog has actually had it is a claim."""
    assert (
        _flags(
            "Nalla is a shepherd mix.",
            fb_caption=f"{_FB}\nNalla gets a cleaning every year. Greenies fill the gap.",
        )
        == []
    )


def test_flags_are_skipped_without_a_mascot_name() -> None:
    assert (
        _flags(
            "Nalla is a shepherd mix.", mascot_name="", fb_caption=f"{_FB}\nNalla eats Greenies."
        )
        == []
    )


def test_the_baseline_plan_raises_no_flags() -> None:
    assert _flags("Nalla is a shepherd mix.") == []
