"""Tests for lib.crew.socialpost -- agent construction, prompt building,
caption rules, output parsing, and the rule-violation retry orchestration.

Same convention as `test_crew_reels.py`: `execute_social_post_crew` is only
covered via a mocked `Crew.kickoff` for the retry-logic branch -- never a
live CrewAI/DeepSeek call. The rules module is pure and tested directly.
"""
# ruff: noqa: S101

from __future__ import annotations

from unittest.mock import MagicMock, patch

from lib.crew.socialpost.agent import build_social_post_agent, build_social_post_task
from lib.crew.socialpost.execute import (
    MAX_ATTEMPTS,
    _parse_structured_output,
    execute_social_post_crew,
)
from lib.crew.socialpost.models import SocialPostPlan
from lib.crew.socialpost.prompts import build_social_post_task_description
from lib.crew.socialpost.rules import (
    KEYWORD_MIN_COVERAGE,
    find_caption_violations,
    first_sentence,
    keyword_coverage,
    keyword_terms,
)

_KW = "bone broth"


def _good_plan(**overrides: str) -> SocialPostPlan:
    fields = {
        "target_question": "Is bone broth safe for dogs?",
        "comment_keyword": "BROTH",
        "fb_caption": (
            "Bone broth is safe for most dogs when made without onions. "
            "Nalla has been drinking it all winter and her coat shows it. "
            "The full no-bones method is on dogfoodandfun.com. "
            "Want the exact recipe? Comment BROTH and I'll DM it to you. "
            "Follow the page for weekly recipes."
        ),
        "ig_caption": (
            "Bone broth is safe for most dogs when made without onions. "
            "Nalla gets a ladle over dinner every night. "
            "Want the recipe? Comment BROTH and I'll DM it to you. "
            "Follow for weekly dog food ideas.\n"
            "#dogfood #bonebroth #dogtreats"
        ),
        "overlay_headline": "BONE BROTH\nFOR DOGS",
        "overlay_subcopy": "The no-bones method",
        "image_brief": "A golden retriever watching a pot simmer in a warm kitchen",
        "cta_ribbon_text": "FULL GUIDE -> DOGFOODANDFUN.COM",
        "image_alt_text": "A dog watching a pot of bone broth simmer on the stove.",
    }
    fields.update(overrides)
    return SocialPostPlan(**fields)


def _plan_json(plan: SocialPostPlan) -> str:
    return plan.model_dump_json()


# ── rules ─────────────────────────────────────────────────────────────────


def test_first_sentence_splits_on_terminator() -> None:
    assert first_sentence("Is it safe? Yes it is.") == "Is it safe?"


def test_good_plan_has_no_violations() -> None:
    assert find_caption_violations(_good_plan(), target_keyword=_KW) == []


def test_off_topic_opener_is_rejected() -> None:
    plan = _good_plan(
        fb_caption="Great news everyone. Bone broth rocks. Comment BROTH? Follow us.",
        ig_caption="Great news everyone. Bone broth rocks. Comment BROTH? Follow.\n#a #b #c",
    )
    violations = find_caption_violations(plan, target_keyword=_KW)
    assert sum("FIRST sentence" in v for v in violations) == 2


def test_natural_phrasing_passes_without_verbatim_keyword() -> None:
    """The relaxation this rule exists for: an SEO keyword pasted in whole
    produces broken English ("What are the raw dog food dangers 2024?"), so
    coverage of its meaningful words is what's required, not the phrase."""
    plan = _good_plan(
        target_question="What are the dangers of raw dog food?",
        comment_keyword="STUDIES",
        fb_caption=(
            "Raw dog food carries a real contamination risk -- 94% of samples in the "
            "studies versus 35% for cooked. Nalla is a shepherd mix and I decided "
            "against it. The full write-up is on dogfoodandfun.com. "
            "Want it? Comment STUDIES and I'll DM you the article. "
            "Follow the page for more."
        ),
        ig_caption=(
            "Raw dog food carries a real contamination risk that the marketing skips. "
            "Nalla does not eat it -- would you take the chance? "
            "Comment STUDIES and I'll DM you the article. Follow for more.\n"
            "#rawdogfood #doghealth #dogfood"
        ),
    )
    # Note: no "2024", and the phrase is never pasted in whole.
    assert find_caption_violations(plan, target_keyword="raw dog food dangers 2024") == []


def test_year_and_stopwords_do_not_count_toward_coverage() -> None:
    assert keyword_terms("raw dog food dangers 2024") == ["raw", "dog", "food", "dangers"]
    assert keyword_terms("best gps for your dog") == ["gps", "dog"]


def test_coverage_matches_on_stem_not_exact_word() -> None:
    """'danger' has to satisfy 'dangers' -- otherwise the rule just forces
    the SEO string's exact inflection back in."""
    terms = keyword_terms("raw dog food dangers 2024")
    assert keyword_coverage("Raw dog food has a real danger nobody mentions", terms) == 1.0


def test_partial_coverage_below_threshold_fails() -> None:
    terms = keyword_terms("raw dog food dangers 2024")  # 4 terms
    assert keyword_coverage("Dog owners deserve better", terms) < KEYWORD_MIN_COVERAGE


def test_url_rejected_in_both_captions() -> None:
    """The core platform constraint: FB and IG reject page posts whose
    caption carries link syntax. Both captions must be URL-free."""
    fb_url = _good_plan(
        fb_caption=(
            "Bone broth is safe for dogs. See https://dogfoodandfun.com/p "
            "-- Nalla approves. Comment BROTH? Follow us."
        )
    )
    v1 = find_caption_violations(fb_url, target_keyword=_KW)
    assert any("fb_caption must contain no URL" in x for x in v1)

    www = _good_plan(
        fb_caption=(
            "Bone broth is safe for dogs. Guide at www.dogfoodandfun.com -- "
            "Nalla approves. Comment BROTH? Follow us."
        )
    )
    v2 = find_caption_violations(www, target_keyword=_KW)
    assert any("fb_caption must contain no URL" in x for x in v2)

    ig_url = _good_plan(
        ig_caption=("Bone broth is safe. See https://x.com/p ok? Comment BROTH. Follow.\n#a #b #c")
    )
    v3 = find_caption_violations(ig_url, target_keyword=_KW)
    assert any("ig_caption must contain no URL" in x for x in v3)


def test_naming_the_site_in_words_is_allowed_on_both() -> None:
    """dogfoodandfun.com in words (no scheme, no www.) is recall, not a link
    -- allowed on BOTH platforms; it's link syntax that gets posts rejected."""
    assert find_caption_violations(_good_plan(), target_keyword=_KW) == []


def test_fb_hashtags_rejected() -> None:
    plan = _good_plan()
    tagged = plan.model_copy(update={"fb_caption": plan.fb_caption + " #dogs"})
    violations = find_caption_violations(tagged, target_keyword=_KW)
    assert any("no hashtags" in v for v in violations)


def test_ig_rejects_bio_phrases() -> None:
    bio = _good_plan(ig_caption="Bone broth is safe. Link in bio! Comment BROTH? Follow.\n#a #b #c")
    violations = find_caption_violations(bio, target_keyword=_KW)
    assert any("link in bio" in v for v in violations)


def test_ig_hashtag_count_bounds() -> None:
    plan = _good_plan()
    too_many = plan.model_copy(update={"ig_caption": plan.ig_caption + " #d #e #f #g"})
    violations = find_caption_violations(too_many, target_keyword=_KW)
    assert any("hashtags" in v for v in violations)


def test_comment_keyword_shape_enforced() -> None:
    for bad in ("", "broth", "BONE BROTH", "BROTH1"):
        plan = _good_plan(comment_keyword=bad)
        violations = find_caption_violations(plan, target_keyword=_KW)
        assert any("comment_keyword must be ONE topical word" in v for v in violations), bad


def test_comment_keyword_must_appear_in_both_captions() -> None:
    plan = _good_plan(
        fb_caption=(
            "Bone broth is safe for most dogs. Nalla loves it -- the full method "
            "is on dogfoodandfun.com. Want it? Follow the page."
        )
    )
    violations = find_caption_violations(plan, target_keyword=_KW)
    assert any("fb_caption must contain the comment-keyword CTA" in v for v in violations)


# ── agent/task construction ───────────────────────────────────────────────


def test_agent_has_no_tools() -> None:
    agent = build_social_post_agent()
    assert agent.tools == []
    assert agent.allow_delegation is False


def test_task_has_no_output_pydantic() -> None:
    agent = build_social_post_agent()
    task = build_social_post_task(agent, "desc")
    assert task.output_pydantic is None
    assert "SocialPostPlan" in task.expected_output


def test_prompt_carries_keyword_domain_and_rules() -> None:
    desc = build_social_post_task_description(
        title="T",
        body="B",
        target_keyword=_KW,
        site_domain="dogfoodandfun.com",
        brand_voice="voice",
    )
    assert _KW in desc
    assert "dogfoodandfun.com" in desc
    assert "no URL" in desc  # the platform constraint is spelled out
    assert "comment_keyword" in desc  # the traffic CTA contract
    assert "link in bio" in desc  # the prohibition is spelled out
    # The prompt must ask for every rule find_caption_violations enforces.
    # It briefly did not ask for a closing question while the rule still
    # required one, so the model could never satisfy it -- two ideas burned
    # all three attempts on 'ig_caption must end with a genuine question'.
    assert desc.count("question mark") >= 2  # demanded for BOTH captions
    assert "voice" in desc
    # The canonical URL must NOT be shown to the model at all -- the safest
    # way to keep it out of the captions is to never hand it over.
    assert "https://" not in desc


# ── parsing ───────────────────────────────────────────────────────────────


def test_parse_valid_json() -> None:
    parsed = _parse_structured_output(_plan_json(_good_plan()), SocialPostPlan, event="t")
    assert parsed is not None
    assert parsed.comment_keyword == "BROTH"


def test_parse_json_embedded_in_prose() -> None:
    raw = f"Thinking about it...\n{_plan_json(_good_plan())}\nDone."
    parsed = _parse_structured_output(raw, SocialPostPlan, event="t")
    assert parsed is not None


def test_parse_returns_none_on_garbage() -> None:
    assert _parse_structured_output("not json at all", SocialPostPlan, event="t") is None
    assert _parse_structured_output(None, SocialPostPlan, event="t") is None


# ── retry orchestration (mocked Crew) ─────────────────────────────────────


def _make_task_with_output(raw: str) -> MagicMock:
    task = MagicMock()
    task.description = "base description"
    task.output.raw = raw
    return task


@patch("crewai.Crew")
def test_execute_returns_plan_when_rules_pass(mock_crew: MagicMock) -> None:
    task = _make_task_with_output(_plan_json(_good_plan()))
    result = execute_social_post_crew(MagicMock(), task, target_keyword=_KW)
    assert result is not None
    assert mock_crew.call_count == 1


@patch("crewai.Crew")
def test_execute_retries_on_rule_violation(mock_crew: MagicMock) -> None:
    bad = _good_plan(fb_caption="Bone broth is safe. See https://x.com/p -- Comment BROTH? Follow.")
    task = _make_task_with_output(_plan_json(bad))

    def fix_on_second_kickoff(*_args: object, **_kwargs: object) -> MagicMock:
        crew_instance = MagicMock()
        if mock_crew.call_count == 2:
            task.output.raw = _plan_json(_good_plan())
        return crew_instance

    mock_crew.side_effect = fix_on_second_kickoff
    result = execute_social_post_crew(MagicMock(), task, target_keyword=_KW)
    assert result is not None
    assert mock_crew.call_count == 2
    assert "YOUR PREVIOUS DRAFT WAS REJECTED" in task.description


@patch("crewai.Crew")
def test_retry_prompt_is_surgical_not_a_redraft(mock_crew: MagicMock) -> None:
    """The live failure this guards: 're-draft the full plan' made the model
    fix the named rule and break a different one. The correction must hand
    the rejected draft back and ask for a minimal edit."""
    bad = _good_plan(fb_caption="Bone broth is safe. See https://x.com/p -- Comment BROTH? Follow.")
    task = _make_task_with_output(_plan_json(bad))
    execute_social_post_crew(MagicMock(), task, target_keyword=_KW)

    assert "Return the SAME plan with ONLY those problems corrected" in task.description
    assert "do not introduce any new rule violation" in task.description.lower()
    # The rejected draft itself is included, so the retry is an edit.
    assert "Bone broth is safe. See https://x.com/p" in task.description


@patch("crewai.Crew")
def test_each_retry_rebuilds_from_the_original_prompt(mock_crew: MagicMock) -> None:
    """Correction blocks must not stack -- two appended blocks would give the
    model contradictory 'previous draft' instructions."""
    bad = _good_plan(
        fb_caption="Bone broth is safe. Still https://x.com/p here. Comment BROTH? Follow."
    )
    task = _make_task_with_output(_plan_json(bad))
    execute_social_post_crew(MagicMock(), task, target_keyword=_KW)

    assert task.description.count("YOUR PREVIOUS DRAFT WAS REJECTED") == 1
    assert task.description.startswith("base description")


@patch("crewai.Crew")
def test_execute_none_after_max_attempts(mock_crew: MagicMock) -> None:
    bad = _good_plan(
        fb_caption="Bone broth is safe. Still https://x.com/p here. Comment BROTH? Follow."
    )
    task = _make_task_with_output(_plan_json(bad))
    result = execute_social_post_crew(MagicMock(), task, target_keyword=_KW)
    assert result is None
    assert mock_crew.call_count == MAX_ATTEMPTS


@patch("crewai.Crew")
def test_execute_succeeds_on_the_second_correction(mock_crew: MagicMock) -> None:
    """One retry was not enough live -- attempt 2 fixed the question and broke
    hashtags. The third attempt is what rescues that idea."""
    bad = _good_plan(fb_caption="Bone broth is safe. See https://x.com/p -- Comment BROTH? Follow.")
    task = _make_task_with_output(_plan_json(bad))

    def fix_on_third_kickoff(*_args: object, **_kwargs: object) -> MagicMock:
        crew_instance = MagicMock()
        if mock_crew.call_count == 3:
            task.output.raw = _plan_json(_good_plan())
        return crew_instance

    mock_crew.side_effect = fix_on_third_kickoff
    result = execute_social_post_crew(MagicMock(), task, target_keyword=_KW)
    assert result is not None
    assert mock_crew.call_count == 3


@patch("crewai.Crew")
def test_execute_none_when_kickoff_raises(mock_crew: MagicMock) -> None:
    mock_crew.side_effect = RuntimeError("network down")
    result = execute_social_post_crew(MagicMock(), _make_task_with_output(""), target_keyword=_KW)
    assert result is None


# ── unparseable output retries (the asymmetry fixed 2026-08-15) ────────────


@patch("crewai.Crew")
def test_execute_retries_when_output_fails_schema_validation(mock_crew: MagicMock) -> None:
    """Live failure: the model returned a plan missing 5 required fields
    (overlay_headline, overlay_subcopy, image_brief, cta_ribbon_text,
    image_alt_text). That abandoned the idea on attempt 1, while captions
    breaking hard rules got all MAX_ATTEMPTS. Drift is exactly what a retry
    fixes, so schema-short output now retries too."""
    task = _make_task_with_output('{"target_question": "Which?", "ig_caption": "..."}')

    def valid_on_second_kickoff(*_args: object, **_kwargs: object) -> MagicMock:
        crew_instance = MagicMock()
        if mock_crew.call_count == 2:
            task.output.raw = _plan_json(_good_plan())
        return crew_instance

    mock_crew.side_effect = valid_on_second_kickoff
    result = execute_social_post_crew(MagicMock(), task, target_keyword=_KW)
    assert result is not None
    assert mock_crew.call_count == 2


@patch("crewai.Crew")
def test_execute_retries_on_unparseable_output_then_gives_up(mock_crew: MagicMock) -> None:
    task = _make_task_with_output("not json at all")
    result = execute_social_post_crew(MagicMock(), task, target_keyword=_KW)
    assert result is None
    assert mock_crew.call_count == MAX_ATTEMPTS


@patch("crewai.Crew")
def test_unparseable_retry_resets_to_the_original_prompt(mock_crew: MagicMock) -> None:
    """There is no rejected draft to hand back, so the retry must re-ask the
    ORIGINAL brief rather than carry stale correction feedback."""
    task = _make_task_with_output("not json at all")
    execute_social_post_crew(MagicMock(), task, target_keyword=_KW)
    assert task.description == "base description"


@patch("crewai.Crew")
def test_kickoff_failure_still_bails_immediately(mock_crew: MagicMock) -> None:
    """A revoked API key retried MAX_ATTEMPTS times is just three 401s and
    triple the time-to-diagnose -- the call never landed, so retrying cannot
    help. Only UNUSABLE ANSWERS retry."""
    mock_crew.side_effect = RuntimeError("401 Authentication Fails")
    result = execute_social_post_crew(MagicMock(), _make_task_with_output(""), target_keyword=_KW)
    assert result is None
    assert mock_crew.call_count == 1
