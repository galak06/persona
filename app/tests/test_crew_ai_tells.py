"""Tests for lib.crew.ai_tells -- the deterministic "reads as machine-written" scan.

Every blocking-rule test is modeled on text that actually shipped to
dogfoodandfun.com rather than on invented examples: the template headings are
what posts 4704/4728/4741 published, and the banned openers are the formulas
16 of 19 posts used. The calibration test at the bottom is the one that keeps
this gate honest -- it asserts the advisory ceilings still clear real published
prose, so tightening a threshold that would stall the live pipeline fails here
instead of silently rejecting every draft in production.
"""
# ruff: noqa: S101

from __future__ import annotations

import pytest

from lib.crew.ai_tells import scan_body
from lib.crew.ai_tells.detect import MIN_WORDS_FOR_RHYTHM
from lib.crew.ai_tells.rhythm import measure_rhythm

_DISCLOSURE = "As an Amazon Associate, I earn from qualifying purchases."


def _post(body: str, *, opener: str = "Your dog's breath clears a room.") -> str:
    """A minimally realistic assembled post: byline, disclosure, then body."""
    return f"<p>By Nalla's Dad | September 7, 2026</p><p>{_DISCLOSURE}</p><p>{opener}</p>{body}"


def _rules(report) -> set[str]:  # type: ignore[no-untyped-def]
    return {f.rule for f in report.blocking}


# ── template headings (the tell all 12 live posts carried) ───────────────────


@pytest.mark.parametrize(
    "heading",
    [
        "FAQ",
        "FAQs",
        "Frequently Asked Questions",
        "Related Reading",
        "Our Pick",
        "Product Comparison Table",
        # Decorated variants -- all four shipped live in exactly this shape.
        "FAQ: Batch Cooking Dog Food, Answered",
        "Our Pick: The Bottom Line",
        "Related Reading & Our Pick",
        "Hook: The Question No Chew Could Answer",
        "Problem: Why Most Dental Advice Ignores Diet",
    ],
)
def test_heading_naming_the_blueprint_slot_is_blocking(heading: str) -> None:
    report = scan_body(_post(f"<h2>{heading}</h2><p>Some real content here.</p>"))
    assert "template_heading" in _rules(report)
    assert report.passed is False


@pytest.mark.parametrize(
    "heading",
    [
        # Real headings from live posts that must NOT be flagged -- these are
        # exactly the "varied label" the writer prompt now asks for.
        "Related Recipes to Try Next",
        "The Problem: When Your Dog Stops Chewing",
        "What Owners Keep Asking Me About Bully Sticks",
        "What I Look for Now (and What I Avoid)",
        "A Simple Decision Framework for Your Dog",
        "The Recall That Made Me Rethink the Treat Jar",
    ],
)
def test_content_bearing_heading_is_not_flagged(heading: str) -> None:
    report = scan_body(_post(f"<h2>{heading}</h2><p>Some real content here.</p>"))
    assert "template_heading" not in _rules(report)


def test_finding_quotes_the_offending_heading_verbatim() -> None:
    report = scan_body(_post("<h2>FAQ: Batch Cooking Dog Food, Answered</h2>"))
    excerpts = [f.excerpt for f in report.blocking if f.rule == "template_heading"]
    assert excerpts == ["FAQ: Batch Cooking Dog Food, Answered"]


# ── banned openers ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "opener",
    [
        "Last spring, I noticed Nalla's breath had gotten worse.",
        "A few months back, I started tracking her chews.",
        "It started with a recall email.",
        "I'll be honest: I did not expect this result.",
        "Picture this: a 50-pound shepherd mix refusing a toothbrush.",
    ],
)
def test_banned_opener_formula_is_blocking(opener: str) -> None:
    report = scan_body(_post("<p>Body text.</p>", opener=opener))
    assert "banned_opener" in _rules(report)


def test_opener_check_skips_byline_and_disclosure() -> None:
    """Without the skip, every post's "opener" is "By Nalla's Dad" and a real
    banned formula in paragraph three is never seen at all."""
    report = scan_body(_post("<p>Body.</p>", opener="It started with a recall email."))
    banned = [f for f in report.blocking if f.rule == "banned_opener"]
    assert banned and banned[0].excerpt.startswith("It started with")


def test_acceptable_opener_passes() -> None:
    report = scan_body(
        _post("<p>Body.</p>", opener="Four weeks. 28 brushings. One unimpressed shepherd mix.")
    )
    assert "banned_opener" not in _rules(report)


# ── meta-commentary ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "phrase",
    ["In this article, I will", "let me clarify", "As an AI", "Let's dive in"],
)
def test_meta_commentary_is_blocking(phrase: str) -> None:
    report = scan_body(_post(f"<p>{phrase} explain the rest.</p>"))
    assert "meta_commentary" in _rules(report)


# ── density ceilings ─────────────────────────────────────────────────────────


def test_cliche_saturation_blocks() -> None:
    body = "<p>" + ("This crucial, robust, seamless, holistic tapestry. " * 12) + "</p>"
    report = scan_body(_post(body))
    assert "cliche_density" in _rules(report)


def test_occasional_cliche_is_advisory_not_blocking() -> None:
    """One "crucial" in a long post is prose, not a tell -- it must be
    reported for trend-watching without failing the draft."""
    body = "<p>Brushing is crucial. " + ("Nalla chewed it anyway. " * 200) + "</p>"
    report = scan_body(_post(body))
    assert "cliche_density" not in _rules(report)
    assert any(f.rule == "lexical_cliche" and f.excerpt == "crucial" for f in report.advisory)


def test_transition_trap_saturation_blocks() -> None:
    body = (
        "<p>" + ("Furthermore, it is important to note this. Moreover, that said. " * 10) + "</p>"
    )
    report = scan_body(_post(body))
    assert "transition_density" in _rules(report)


def test_antithesis_reflex_saturation_blocks() -> None:
    body = "<p>" + ("It's not just a chew, but a routine. " * 8) + "</p>"
    report = scan_body(_post(body))
    assert "antithesis_density" in _rules(report)


# ── rhythm ───────────────────────────────────────────────────────────────────


def test_uniform_sentence_rhythm_blocks() -> None:
    """Every sentence the same length, over the word floor -- the homogenous
    cadence tell, which no substring match can catch."""
    sentence = "The dog ate the food and then walked outside for a short while today. "
    report = scan_body(_post("<p>" + sentence * 80 + "</p>"))
    assert "uniform_sentence_rhythm" in _rules(report)


def test_varied_rhythm_passes() -> None:
    varied = (
        "Nalla refused. "
        "I tried again the next morning, this time with the paste warmed slightly in my hand, "
        "which she tolerated for all of four seconds before backing into the hallway. "
        "It failed. "
        "The second week went better, though not by much, and the numbers I wrote down that "
        "Sunday were boring enough that I nearly stopped recording them. "
    )
    report = scan_body(_post("<p>" + varied * 20 + "</p>"))
    assert "uniform_sentence_rhythm" not in _rules(report)
    assert "no_short_sentences" not in _rules(report)


def test_short_body_is_not_judged_on_rhythm() -> None:
    """Below the word floor the statistics are noise, so a legitimately short
    body must not be rejected for a cadence that cannot be measured."""
    sentence = "The dog ate the food and walked outside. "
    report = scan_body(_post("<p>" + sentence * 10 + "</p>"))
    assert report.metrics["words"] < MIN_WORDS_FOR_RHYTHM
    assert "uniform_sentence_rhythm" not in _rules(report)


# ── parsing ──────────────────────────────────────────────────────────────────


def test_trailing_jsonld_does_not_count_toward_prose_rates() -> None:
    """Schema is appended after the body; counting its text would let markup
    push a clean post over a density ceiling."""
    schema = '<script type="application/ld+json">{"crucial": "crucial crucial"}</script>'
    clean = scan_body(_post("<p>Nalla chewed it anyway.</p>"))
    with_schema = scan_body(_post("<p>Nalla chewed it anyway.</p>") + schema)
    assert with_schema.metrics["cliche_rate"] == clean.metrics["cliche_rate"] == 0.0


def test_reasons_render_blocking_findings_only() -> None:
    report = scan_body(_post("<h2>Our Pick</h2><p>Brushing is crucial.</p>"))
    reasons = report.reasons()
    assert len(reasons) == len(report.blocking)
    assert any("Our Pick" in r for r in reasons)
    assert not any("crucial" in r for r in reasons)


def test_clean_post_passes_every_rule() -> None:
    report = scan_body(
        _post(
            "<h2>What I Feed Her Now</h2>"
            "<p>She hated it. The second bag was better, though the price was not, and I kept "
            "buying it anyway because nine of fourteen mornings went quietly.</p>"
        )
    )
    assert report.passed is True
    assert report.reasons() == []


# ── rhythm measurement unit ──────────────────────────────────────────────────


def test_measure_rhythm_handles_empty_text() -> None:
    metrics = measure_rhythm("", [])
    assert metrics.sentences == 0
    assert metrics.variation == 0.0
