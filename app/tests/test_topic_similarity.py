"""Unit tests for lib.crew.topic_similarity -- is_similar_topic.

Direct tests of the fuzzy-dedup predicate itself, independent of
`run_crew_scout`'s integration tests in test_crew_scout.py /
test_crew_scout_auto_approve.py.
"""
# ruff: noqa: S101

from __future__ import annotations

from lib.crew.topic_similarity import is_similar_topic, subject_tokens


def test_is_similar_topic_exact_match_case_insensitive() -> None:
    existing = {"best dog food for sensitive stomachs"}
    assert is_similar_topic("Best Dog Food For Sensitive Stomachs", existing) is True


def test_is_similar_topic_fuzzy_match_above_threshold() -> None:
    existing = {"homemade dog food recipes for beginners"}
    assert is_similar_topic("Homemade Dog Food Recipe for Beginners", existing) is True


def test_is_similar_topic_distinct_topic_below_threshold() -> None:
    existing = {"homemade dog food recipes for beginners"}
    assert is_similar_topic("Best GPS Trackers for Anxious Dogs", existing) is False


def test_is_similar_topic_empty_existing_returns_false() -> None:
    assert is_similar_topic("Anything At All", set()) is False


def test_is_similar_topic_custom_threshold_changes_outcome() -> None:
    existing = {"how to fix your dog's gut"}
    borderline = "Fixing Your Dog's Gut Issues"

    assert is_similar_topic(borderline, existing, threshold=0.85) is False
    assert is_similar_topic(borderline, existing, threshold=0.6) is True


# --------------------------------------------------------------- subject match
# Every pair below is taken verbatim from the 36 live `content_ideas` rows.
# All four "same subject" pairs were inserted as separate ideas because the
# whole-string check scored them 0.37-0.63, far under its 0.85 gate.


def test_same_subject_different_headline_is_a_duplicate() -> None:
    """The pair that motivated this check: 0.63 on whole-string difflib."""
    existing = {"pumpkin vs sweet potato for dog digestion: which one fixed nalla's runny poop?"}
    assert (
        is_similar_topic(
            "Sweet Potato vs. Pumpkin for Dogs: Which One Helped Nalla's Upset Stomach?", existing
        )
        is True
    )


def test_identical_head_matches_even_when_too_short_for_the_size_floor() -> None:
    """ "Canicross for Beginners" is only {canicross, beginner} -- two tokens,
    under `_MIN_SUBJECT_TOKENS`. The equal-set rule is what catches it; without
    that rule the most blatant duplicate in the live table slips through."""
    existing = {"canicross for beginners: how nalla and i started running with a pulling harness"}
    assert (
        is_similar_topic("Canicross for Beginners: The 5 Gear Mistakes We Made", existing) is True
    )


def test_recall_pair_with_reordered_head_is_a_duplicate() -> None:
    existing = {"dog food recall 2026: what nalla's dad checks before feeding raw"}
    assert (
        is_similar_topic("The 2026 Dog Food Recall Cheat Sheet: What to Stop Feeding", existing)
        is True
    )


def test_stub_topic_does_not_swallow_a_whole_subject_area() -> None:
    """The literal row "Dog food" reduces to {dog, food}, which overlaps every
    food headline this brand writes -- it scores 0.67 against the raw-food
    post. The size floor is what stops one short row suppressing the rest."""
    existing = {"dog food"}
    assert is_similar_topic("Raw Dog Food: What the 2025 Studies Actually Say", existing) is False


def test_shared_generic_words_alone_are_not_a_duplicate() -> None:
    """Both are "homemade ... guide" for dogs, sharing 3 of 6 tokens (0.50),
    but meals and treats are genuinely different posts."""
    existing = {"homemade dog food meals guide"}
    assert is_similar_topic("Homemade dog treats guide", existing) is False


def test_subject_tokens_drops_the_hook_after_the_colon() -> None:
    """The head carries the subject; the tail is the part the agent rewrites
    every run, so it must not contribute to the comparison."""
    assert subject_tokens(
        "Bone Broth Toppers: Our Slow-Cooker Recipe for Picky Eaters"
    ) == frozenset({"bone", "broth", "topper"})


def test_subject_tokens_singularises_so_plurals_do_not_split_a_match() -> None:
    assert subject_tokens("Sweet Potato for Dogs") == subject_tokens("Sweet Potatoes for Dog")
