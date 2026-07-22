"""Tests for comment_generator.py — scoring and voice validation."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

from comment_generator import build_claude_prompt, score_relevance, validate_voice
from lib.config import settings

# ── score_relevance ──────────────────────────────────────────────────────────


class TestScoreRelevance:
    """Tests for the relevance scoring algorithm."""

    def test_food_post_scores_high(self):
        post = "My dog has been on a raw diet for 3 months, what protein should I try next?"
        score = score_relevance(post, group_category="food")
        assert score >= 0.70, f"Food post with question scored too low: {score}"

    def test_gps_post_scores_high(self):
        post = "Just got a Tractive GPS tracker for our hiking trips. Battery lasts 2 days."
        score = score_relevance(post)
        assert score >= 0.50, f"GPS post scored too low: {score}"

    def test_irrelevant_post_scores_low(self):
        post = "Just adopted a new kitten! So excited!"
        score = score_relevance(post)
        assert score < 0.40, f"Irrelevant post scored too high: {score}"

    def test_question_format_bonus(self):
        without_q = "We feed our dog raw chicken and rice every day."
        with_q = "We feed our dog raw chicken and rice every day. Is this enough?"
        assert score_relevance(with_q) > score_relevance(without_q)

    def test_reviewed_brand_bonus(self):
        without_brand = "I'm looking for a new GPS collar for my dog."
        with_brand = "I'm looking at the Fi collar for my dog."
        assert score_relevance(with_brand) > score_relevance(without_brand)

    def test_crowded_post_penalty(self):
        meta_low = {"comment_count": 10, "hours_old": 12}
        meta_high = {"comment_count": 150, "hours_old": 12}
        post = "What homemade dog food recipe do you use?"
        score_low = score_relevance(post, meta_low)
        score_high = score_relevance(post, meta_high)
        assert score_low > score_high, "Crowded post should score lower"

    def test_competitor_penalty(self):
        meta = {"is_competitor": True, "comment_count": 5, "hours_old": 6}
        post = "Check out our new dog food subscription service!"
        score = score_relevance(post, meta)
        assert score < 0.40, f"Competitor post scored too high: {score}"

    def test_fresh_post_bonus(self):
        old = {"comment_count": 10, "hours_old": 48}
        fresh = {"comment_count": 10, "hours_old": 6}
        post = "Just switched to homemade food, any tips?"
        assert score_relevance(post, fresh) > score_relevance(post, old)

    def test_food_group_context_bonus(self):
        post = "My dog has been itching a lot lately"
        score_general = score_relevance(post, group_category="general")
        score_food = score_relevance(post, group_category="food")
        assert score_food >= score_general

    def test_empty_post_scores_zero(self):
        assert score_relevance("") == 0.0

    def test_score_capped_reasonable(self):
        """Score should not exceed ~1.5 even with all signals."""
        post = "Has anyone tried the Fi collar GPS tracker for raw fed dogs on trail hikes?"
        meta = {"comment_count": 20, "hours_old": 2}
        score = score_relevance(post, meta, group_category="food")
        assert score <= 1.5, f"Score unreasonably high: {score}"

    def test_returns_float(self):
        score = score_relevance("any text")
        assert isinstance(score, float)

    def test_multiple_food_keywords_still_040(self):
        """Food keywords should give 0.40 regardless of how many match."""
        post = "homemade recipe nutrition protein kibble diet"
        score = score_relevance(post)
        # Food = 0.40, no question, no brand, no meta
        assert score == 0.40


# ── validate_voice ───────────────────────────────────────────────────────────


class TestValidateVoice:
    """Tests for brand voice validation rules."""

    def test_valid_comment_passes(self):
        comment = (
            "We dealt with something similar with Nalla — constant paw licking "
            "that three different vets couldn't pin down. Switching to a simple "
            "homemade base helped us isolate the trigger. Have you tried an "
            "elimination diet?"
        )
        valid, violations = validate_voice(comment)
        assert valid, f"Valid comment failed: {violations}"

    def test_medical_jargon_rejected(self):
        comment = (
            "Clinical studies show that this treatment is veterinary-grade "
            "and scientifically formulated. What do you think?"
        )
        valid, violations = validate_voice(comment)
        assert not valid
        assert any("Medical jargon" in v for v in violations)

    def test_salesy_language_rejected(self):
        comment = (
            "We've tried this with Nalla and it worked great! "
            "Check out our website for more tips. What protein do you use?"
        )
        valid, violations = validate_voice(comment)
        assert not valid
        assert any("Salesy" in v for v in violations)

    def test_persona_url_rejected(self):
        # Engagement comments must never carry the brand's OWN site URL.
        # validate_voice blocks whatever brand is loaded (settings.site.url),
        # so the test references that live value rather than a hardcoded
        # placeholder that only matches one brand.
        comment = (
            f"We wrote about this at {settings.site.url} — Nalla loved it. "
            "What recipe are you using?"
        )
        valid, violations = validate_voice(comment)
        assert not valid
        assert any(settings.site.url in v for v in violations)

    def test_persona_url_allowed_when_flag_set(self):
        """Brand publishers (FB group posts, FB page link cards, IG carousel
        captions) bake the URL into the body — allow_own_url=True must let
        it through with no violations."""
        caption = (
            "Try this homemade kibble recipe — Nalla loved it. "
            "Full guide at your-brand.com/recipes/x. What protein do you use?"
        )
        valid, violations = validate_voice(caption, allow_own_url=True)
        assert valid, f"Brand caption with own URL was rejected: {violations}"

    def test_other_salesy_phrases_still_blocked_when_flag_set(self):
        """allow_own_url=True must ONLY whitelist the brand URL — every
        other salesy phrase ("buy now", "shop now", etc.) must still fail."""
        comment = (
            "We tried this with Nalla last year and it worked great. "
            "Buy now at your-brand.com/shop. What protein do you use?"
        )
        valid, violations = validate_voice(comment, allow_own_url=True)
        assert not valid
        assert any("buy now" in v.lower() for v in violations)

    def test_no_question_rejected(self):
        comment = (
            "We dealt with this with Nalla last year. Switching to "
            "turkey helped a lot and she's been fine since."
        )
        valid, violations = validate_voice(comment)
        assert not valid
        assert any("question" in v.lower() for v in violations)

    def test_too_short_rejected(self):
        valid, violations = validate_voice("Nice! How's it going?")
        assert not valid
        assert any("too short" in v.lower() for v in violations)

    def test_too_long_rejected(self):
        long_comment = "We tried this with Nalla. " * 30 + "What do you think?"
        valid, violations = validate_voice(long_comment)
        assert not valid
        assert any("too long" in v.lower() for v in violations)

    def test_generic_opener_rejected(self):
        comment = (
            "Great post! We've been doing homemade food for Nalla for "
            "about 6 months now and it's been amazing. What recipe do you use?"
        )
        valid, violations = validate_voice(comment)
        assert not valid
        assert any("Generic opener" in v for v in violations)

    def test_lacks_specificity_rejected(self):
        comment = (
            "This is really interesting and helpful for dog owners "
            "who are looking into this kind of thing. What do you think?"
        )
        valid, violations = validate_voice(comment)
        assert not valid
        assert any("specificity" in v.lower() for v in violations)

    def test_lacks_personal_experience_rejected(self):
        comment = (
            "Dogs generally do better on a raw diet with variety in "
            "protein sources over 10 days. What protein are you using?"
        )
        valid, violations = validate_voice(comment)
        assert not valid
        assert any("personal experience" in v.lower() for v in violations)

    def test_brand_mention_counts_as_specific(self):
        comment = (
            "We've been using Tractive for about 3 months now and the "
            "GPS accuracy on trails has been solid. How's the battery "
            "life been for you?"
        )
        valid, violations = validate_voice(comment)
        assert valid, f"Brand + timeframe should pass: {violations}"

    def test_number_counts_as_specific(self):
        comment = (
            "We noticed a big difference after about 2 weeks of switching "
            "our approach. It took patience but the results were clear. "
            "How long have you been trying this?"
        )
        valid, violations = validate_voice(comment)
        assert valid, f"Number should count as specific: {violations}"

    def test_multiple_violations_all_reported(self):
        comment = "Great post! Check out our website."
        valid, violations = validate_voice(comment)
        assert not valid
        assert len(violations) >= 3  # generic opener, salesy, too short, no question, etc.


# ── score_relevance: config-driven keywords (brand generalization) ──────────


class TestScoreRelevanceCharacterization:
    """Regression gate for the content_analysis.keywords rename
    (food_nutrition/active_gps/brands_reviewed ->
    primary_keywords/secondary_keywords/competitor_mentions) and the switch
    from hardcoded Python lists to config-driven lookups.

    Expected scores were captured by running the PRE-rename score_relevance
    (git history, before comment_generator_defaults.py existed) against the
    live dogfoodandfun/config.json for each case below, then verified
    identical against the POST-rename code + renamed/expanded config. Any
    value here diverging from a live run means dogfoodandfun's scoring
    behavior silently changed — treat as a hard regression, never "fix" by
    updating the expected number.
    """

    CASES: ClassVar[list[tuple[str, str, dict | None, str, float]]] = [
        (
            "food_with_question",
            "My dog has been on a raw diet for 3 months, what protein should I try next?",
            None,
            "food",
            0.75,
        ),
        (
            "gps_no_question",
            "Just got a Tractive GPS tracker for our hiking trips. Battery lasts 2 days.",
            None,
            "",
            0.5,
        ),
        ("irrelevant", "Just adopted a new kitten! So excited!", None, "", 0.0),
        (
            "competitor_meta",
            "Check out our new dog food subscription service!",
            {"is_competitor": True, "comment_count": 5, "hours_old": 6},
            "",
            0.1,
        ),
        (
            "crowded_food",
            "What homemade dog food recipe do you use?",
            {"comment_count": 150, "hours_old": 12},
            "",
            0.4,
        ),
        (
            "light_food",
            "What homemade dog food recipe do you use?",
            {"comment_count": 10, "hours_old": 12},
            "",
            0.8,
        ),
        (
            "competitor_brand_mention",
            "I'm looking at the Fi collar for my dog.",
            None,
            "",
            0.5,
        ),
        (
            "all_signals",
            "Has anyone tried the Fi collar GPS tracker for raw fed dogs on trail hikes?",
            {"comment_count": 20, "hours_old": 2},
            "food",
            1.45,
        ),
        ("empty", "", None, "", 0.0),
        ("health_group_itch", "My dog has been itching a lot lately", None, "health", 0.0),
        ("gps_group_bonus", "Just got new gear for our trail walks", None, "gps", 0.45),
    ]

    def test_scores_unchanged_after_config_rename(self):
        for name, text, meta, category, expected in self.CASES:
            actual = score_relevance(text, meta, group_category=category)
            assert actual == expected, (
                f"{name}: expected {expected}, got {actual} — "
                "dogfoodandfun scoring behavior changed"
            )


class TestScoreRelevanceCrossBrandIsolation:
    """A differently-themed brand's config must not score dog-food text high
    off leftover Python defaults — proves no cross-brand keyword leakage."""

    def test_dog_food_text_scores_near_zero_for_unrelated_brand(self, monkeypatch):
        monkeypatch.setattr(
            settings.content_analysis,
            "keywords",
            {
                "primary_keywords": ["espresso", "latte", "roast"],
                "secondary_keywords": ["grinder", "portafilter"],
                "competitor_mentions": ["blue bottle", "peets"],
            },
        )
        post = "Homemade raw kibble diet with extra protein and bone broth for my dog."
        score = score_relevance(post, group_category="")
        assert score == 0.0, f"Unrelated-brand config leaked dog-food scoring: {score}"

    def test_explicit_empty_lists_do_not_fall_back_to_defaults(self, monkeypatch):
        """An empty list for a real onboarded brand is a deliberate 'no bonus
        yet' state — the fallback must NOT fire just because the list is
        empty (only when the key is missing entirely)."""
        monkeypatch.setattr(
            settings.content_analysis,
            "keywords",
            {"primary_keywords": [], "secondary_keywords": [], "competitor_mentions": []},
        )
        post = "Homemade raw kibble diet with extra protein and a Tractive GPS tracker."
        score = score_relevance(post, group_category="")
        assert score == 0.0, f"Empty-but-present keys wrongly fell back to defaults: {score}"


class TestScoreRelevanceMissingKeyFallback:
    """A config.json that omits content_analysis.keywords entirely must still
    score using the Python DEFAULT_* constants in comment_generator_defaults.py."""

    def test_missing_keywords_dict_uses_defaults(self, monkeypatch):
        monkeypatch.setattr(settings.content_analysis, "keywords", {})
        post = "My dog has been on a raw diet for 3 months, what protein should I try next?"
        score = score_relevance(post, group_category="food")
        # Same post/category as the "food_with_question" characterization
        # case (0.75) — proves DEFAULT_* constants reproduce identical
        # scoring when the config key is missing outright.
        assert score == 0.75, f"Missing-key fallback did not use defaults: {score}"


class TestOnboardedConfigScoresNonDegenerate:
    """End-to-end guard for the onboarding regression: a brand provisioned
    WITHOUT hand-curated keywords must still score posts usefully.

    render_config_json used to write empty `[]` keyword lists, which shadowed
    the DEFAULT_* lists (present-but-empty never falls back) and collapsed
    every relevance score to ~0, so scanners queued nothing. The template now
    OMITS empty keyword categories, so score_relevance falls back to the broad
    DEFAULT_* lists for a keyword-less brand. This test ties the rendered
    config to actual scoring, not just its shape.
    """

    def test_keywordless_rendered_config_uses_defaults(self, monkeypatch):
        from lib.brand_templates import BrandSpec, render_config_json

        spec = BrandSpec(name="Fresh Co", site_url="https://fresh.example", niche="dog food")
        rendered = render_config_json(spec)
        # No keyword categories supplied -> empty keywords dict.
        assert rendered["content_analysis"]["keywords"] == {}
        monkeypatch.setattr(
            settings.content_analysis,
            "keywords",
            rendered["content_analysis"]["keywords"],
        )
        post = "My dog has been on a raw diet for 3 months, what protein should I try next?"
        score = score_relevance(post, group_category="food")
        # Same post/category as the "food_with_question" characterization case
        # (0.75) -- proves a freshly onboarded, keyword-less brand no longer
        # produces the degenerate 0-score behavior.
        assert score == 0.75, f"Onboarded keyword-less config scored degenerate: {score}"


# ── build_claude_prompt ──────────────────────────────────────────────────────


class TestBuildClaudePrompt:
    """Tests for prompt construction."""

    def test_includes_post_text(self):
        prompt = build_claude_prompt("My dog loves salmon", "food", "Dog Food Group")
        assert "My dog loves salmon" in prompt

    def test_includes_category(self):
        prompt = build_claude_prompt("test post", "gps", "GPS Group")
        assert "gps" in prompt

    def test_includes_group_name(self):
        prompt = build_claude_prompt("test post", "food", "Homemade Dog Food")
        assert "Homemade Dog Food" in prompt

    def test_truncates_long_post(self):
        long_post = "x" * 2000
        prompt = build_claude_prompt(long_post, "food")
        # Post text should be truncated to 1000 chars
        assert len(prompt) < 4000
