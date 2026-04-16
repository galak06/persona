"""Tests for comment_generator.py — scoring and voice validation."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

from comment_generator import build_claude_prompt, score_relevance, validate_voice

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

    def test_dogfoodandfun_url_rejected(self):
        comment = (
            "We wrote about this at dogfoodandfun.com — Nalla loved it. What recipe are you using?"
        )
        valid, violations = validate_voice(comment)
        assert not valid
        assert any("dogfoodandfun.com" in v for v in violations)

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
