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
from lib.crew.socialpost.execute import _parse_structured_output, execute_social_post_crew
from lib.crew.socialpost.models import SocialPostPlan
from lib.crew.socialpost.prompts import build_social_post_task_description
from lib.crew.socialpost.rules import find_caption_violations, first_sentence

_URL = "https://dogfoodandfun.com/p/bone-broth"
_KW = "bone broth"


def _good_plan(**overrides: str) -> SocialPostPlan:
    fields = {
        "target_question": "Is bone broth safe for dogs?",
        "fb_caption": (
            "Bone broth is safe for most dogs when made without onions. "
            "Nalla has been drinking it all winter and her coat shows it. "
            f"I wrote up the exact no-bones method here: {_URL} "
            "Have you ever simmered a batch for your dog? "
            "Follow the page for weekly recipes."
        ),
        "ig_caption": (
            "Bone broth is safe for most dogs when made without onions. "
            "Nalla gets a ladle over dinner every night. "
            "Would your dog go for it? "
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
    assert find_caption_violations(_good_plan(), target_keyword=_KW, post_url=_URL) == []


def test_keyword_must_be_in_first_sentence_of_both() -> None:
    plan = _good_plan(
        fb_caption=f"Great news everyone. Bone broth rocks: {_URL} Right? Follow us.",
        ig_caption="Great news everyone. Bone broth rocks. Right? Follow.\n#a #b #c",
    )
    violations = find_caption_violations(plan, target_keyword=_KW, post_url=_URL)
    assert sum("FIRST sentence" in v for v in violations) == 2


def test_fb_url_required_exactly_once_never_in_hook() -> None:
    no_url = _good_plan(fb_caption="Bone broth is safe. Nalla loves it. Really? Follow.")
    v1 = find_caption_violations(no_url, target_keyword=_KW, post_url=_URL)
    assert any("must contain the post URL" in x for x in v1)

    doubled = _good_plan()
    doubled_fb = doubled.fb_caption + f" Again: {_URL}"
    v2 = find_caption_violations(
        doubled.model_copy(update={"fb_caption": doubled_fb}),
        target_keyword=_KW,
        post_url=_URL,
    )
    assert any("exactly once" in x for x in v2)

    hook_url = _good_plan(
        fb_caption=f"Bone broth guide at {_URL} is live! Nalla approves. Right? Follow."
    )
    v3 = find_caption_violations(hook_url, target_keyword=_KW, post_url=_URL)
    assert any("first sentence" in x for x in v3)


def test_fb_hashtags_rejected() -> None:
    plan = _good_plan()
    tagged = plan.model_copy(update={"fb_caption": plan.fb_caption + " #dogs"})
    violations = find_caption_violations(tagged, target_keyword=_KW, post_url=_URL)
    assert any("no hashtags" in v for v in violations)


def test_ig_rejects_urls_and_bio_phrases() -> None:
    with_url = _good_plan()
    v1 = find_caption_violations(
        with_url.model_copy(
            update={"ig_caption": f"Bone broth is safe. See {_URL} ok? Follow.\n#a #b #c"}
        ),
        target_keyword=_KW,
        post_url=_URL,
    )
    assert any("no URL" in x for x in v1)

    bio = _good_plan()
    v2 = find_caption_violations(
        bio.model_copy(
            update={"ig_caption": "Bone broth is safe. Link in bio! ok? Follow.\n#a #b #c"}
        ),
        target_keyword=_KW,
        post_url=_URL,
    )
    assert any("link in bio" in x for x in v2)


def test_ig_hashtag_count_bounds() -> None:
    plan = _good_plan()
    too_many = plan.model_copy(update={"ig_caption": plan.ig_caption + " #d #e #f #g"})
    violations = find_caption_violations(too_many, target_keyword=_KW, post_url=_URL)
    assert any("hashtags" in v for v in violations)


def test_naming_the_site_in_words_is_allowed_on_ig() -> None:
    """dogfoodandfun.com in words (no scheme, no www.) is recall, not a link."""
    plan = _good_plan()
    named = plan.model_copy(
        update={
            "ig_caption": (
                "Bone broth is safe for most dogs. Full guide on dogfoodandfun.com -- "
                "Nalla approves. Would yours? Follow for more.\n#dogfood #broth #dogs"
            )
        }
    )
    assert find_caption_violations(named, target_keyword=_KW, post_url=_URL) == []


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


def test_prompt_carries_keyword_url_and_domain() -> None:
    desc = build_social_post_task_description(
        title="T",
        body="B",
        target_keyword=_KW,
        post_url=_URL,
        site_domain="dogfoodandfun.com",
        brand_voice="voice",
    )
    assert _KW in desc
    assert _URL in desc
    assert "link in bio" in desc  # the prohibition is spelled out
    assert "voice" in desc


# ── parsing ───────────────────────────────────────────────────────────────


def test_parse_valid_json() -> None:
    parsed = _parse_structured_output(_plan_json(_good_plan()), SocialPostPlan, event="t")
    assert parsed is not None
    assert parsed.target_question.startswith("Is bone broth")


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
    result = execute_social_post_crew(MagicMock(), task, target_keyword=_KW, post_url=_URL)
    assert result is not None
    assert mock_crew.call_count == 1


@patch("crewai.Crew")
def test_execute_retries_once_on_rule_violation(mock_crew: MagicMock) -> None:
    bad = _good_plan(fb_caption="Bone broth is safe. No link here at all. Right? Follow.")
    task = _make_task_with_output(_plan_json(bad))

    def fix_on_second_kickoff(*_args: object, **_kwargs: object) -> MagicMock:
        crew_instance = MagicMock()
        if mock_crew.call_count == 2:
            task.output.raw = _plan_json(_good_plan())
        return crew_instance

    mock_crew.side_effect = fix_on_second_kickoff
    result = execute_social_post_crew(MagicMock(), task, target_keyword=_KW, post_url=_URL)
    assert result is not None
    assert mock_crew.call_count == 2
    assert "PREVIOUS DRAFT WAS REJECTED" in task.description


@patch("crewai.Crew")
def test_execute_none_when_retry_still_violates(mock_crew: MagicMock) -> None:
    bad = _good_plan(fb_caption="Bone broth is safe. Still no link. Right? Follow.")
    task = _make_task_with_output(_plan_json(bad))
    result = execute_social_post_crew(MagicMock(), task, target_keyword=_KW, post_url=_URL)
    assert result is None
    assert mock_crew.call_count == 2


@patch("crewai.Crew")
def test_execute_none_when_kickoff_raises(mock_crew: MagicMock) -> None:
    mock_crew.side_effect = RuntimeError("network down")
    result = execute_social_post_crew(
        MagicMock(), _make_task_with_output(""), target_keyword=_KW, post_url=_URL
    )
    assert result is None
