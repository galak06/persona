"""Voice-contract drift guard for the wired skill prompts.

Since Slice 6 the engagement flows carry NO Python-side voice/length/decision
text: ``lib.draft_helper`` sends the flow's rendered ``## LLM Prompt`` section
as the SYSTEM prompt and only per-post context as the USER prompt. That makes
the SKILL.md files the single source of truth — and a well-meaning edit to one
of them can now silently stop asking the model for something that
``lib.comment_generator.validate_voice`` still rejects at validation time
(every such draft is dropped, so the failure mode is silent starvation, not a
crash).

These tests load the REAL skill files, render them against the ci_brand
fixture config, and assert each prompt still asks for what the validator
enforces:

  * a question ("Comment must end with a question")
  * no salesy phrasing / no links (blocked_salesy_phrases + the site URL)
  * no medical or clinical claims (blocked_medical_terms)
  * no generic openers (blocked_generic_openers)
  * a length ceiling at or under max_comment_length
  * specificity + first-person experience (mascot / personal-experience rules)

plus: no unrendered ``{{`` residue, and — for the four engagement flows —
the engage/decline bullets and the flow's own length rule.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lib import skill_loader
from lib.config import settings

_CI_BRAND = Path(__file__).resolve().parent / "fixtures" / "ci_brand"
# Resolved at import time: test_skill_drafter monkeypatches this function.
_SKILLS_DIR = skill_loader.default_skills_dir()
_BRAND = skill_loader.load_brand_vars(str(_CI_BRAND))

# The four flows whose drafts run through draft_helper.SkillDrafter.
_ENGAGEMENT_SKILLS = ("ig-comment", "fb-engager", "ig-engager", "wp-comment-handler")
# ...plus the reply/first-touch drafter (lib/reply_drafter.py).
_WIRED_SKILLS = (*_ENGAGEMENT_SKILLS, "comment-composer")

# Each flow's persona line must name the surface it actually posts on — the
# ig-comment line has been byte-copied into new skills before.
_FRAMING = {
    "ig-comment": "commenting on Instagram",
    "ig-engager": "commenting on Instagram",
    "fb-engager": "commenting in Facebook groups",
    "wp-comment-handler": "replying to reader comments on our own blog",
}

# The length rule the flow's Python entry point relies on (it no longer
# restates one): fb_engager.py's inline commenter calls
# draft_short_comment_for_post.
_LENGTH_RULES = {
    "ig-comment": "a single short reply (1-3 sentences)",
    "ig-engager": "a single short reply (1-3 sentences)",
    "wp-comment-handler": "a single short reply (1-3 sentences)",
    "fb-engager": "ONE short sentence (15-25 words)",
}

_DECISION_BULLETS = (
    "Decline (engage=false) if:",
    "generic/low-effort and a reply would feel like spam",
    "no authentic, specific angle on this exact post",
    "the post is from a competitor account",
    "repetitive or forced rather than genuine",
)


def _rendered(skill: str) -> str:
    return skill_loader.load_skill_prompt(skill, skills_dir=_SKILLS_DIR, brand=_BRAND)


@pytest.mark.parametrize("skill", _WIRED_SKILLS)
def test_prompt_renders_without_placeholder_residue(skill: str) -> None:
    """A surviving ``{{`` would be sent to the model verbatim."""
    prompt = _rendered(skill)
    assert "{{" not in prompt and "}}" not in prompt
    for value in (_BRAND.name, _BRAND.domain, _BRAND.persona, _BRAND.mascot):
        assert value in prompt
    # The mascot must come from the brand config, never a hardcoded name.
    assert "Nalla" not in prompt


@pytest.mark.parametrize("skill", _WIRED_SKILLS)
def test_prompt_requires_a_genuine_question(skill: str) -> None:
    """validate_voice rejects any comment without a '?'."""
    prompt = _rendered(skill)
    assert "End with one specific question tied to what they said" in prompt
    assert 'not "what do you think?"' in prompt


@pytest.mark.parametrize("skill", _WIRED_SKILLS)
def test_prompt_bans_links_and_salesy_language(skill: str) -> None:
    """validate_voice rejects blocked_salesy_phrases and the brand's own URL
    (engagement paths always pass allow_own_url=False)."""
    prompt = _rendered(skill)
    lowered = prompt.lower()
    named = [p for p in settings.voice_validation.blocked_salesy_phrases if p.lower() in lowered]
    assert len(named) >= 2, f"{skill}: prompt names no configured salesy phrases"
    assert "no links" in lowered or "never paste a url" in lowered


@pytest.mark.parametrize("skill", _WIRED_SKILLS)
def test_prompt_bans_medical_and_clinical_claims(skill: str) -> None:
    """validate_voice rejects blocked_medical_terms outright."""
    lowered = _rendered(skill).lower()
    assert "medical claims" in lowered
    assert "not clinical" in lowered
    assert "i'm a vet" in lowered


@pytest.mark.parametrize("skill", _WIRED_SKILLS)
def test_prompt_bans_generic_openers(skill: str) -> None:
    """validate_voice rejects a comment starting with a blocked opener, so the
    prompt must ban them AND quote examples the validator actually blocks."""
    lowered = _rendered(skill).lower()
    assert "no generic praise" in lowered
    quoted = [o for o in settings.voice_validation.blocked_generic_openers if o.lower() in lowered]
    assert len(quoted) >= 2, f"{skill}: prompt quotes no blocked generic openers"


@pytest.mark.parametrize("skill", _WIRED_SKILLS)
def test_prompt_length_ceiling_is_inside_the_validator_window(skill: str) -> None:
    """The prompt's char ceiling must sit inside validate_voice's own window,
    or the model is being asked for drafts the validator will always drop."""
    prompt = _rendered(skill)
    match = re.search(r"under (\d+) chars", prompt)
    assert match is not None, f"{skill}: prompt states no char ceiling"
    ceiling = int(match.group(1))
    assert settings.voice_validation.min_comment_length < ceiling
    assert ceiling <= settings.voice_validation.max_comment_length
    assert "1-3 sentences" in prompt


@pytest.mark.parametrize("skill", _WIRED_SKILLS)
def test_prompt_requires_specificity_and_first_person(skill: str) -> None:
    """validate_voice requires a specific detail (mascot / number / brand /
    timeframe) AND a first-person experience claim."""
    prompt = _rendered(skill)
    assert "Mention the brand mascot by name" in prompt
    assert "first-person" in prompt
    assert "NEVER invent facts" in prompt


# ------------------------------------------------------- engagement flows only


@pytest.mark.parametrize("skill", _ENGAGEMENT_SKILLS)
def test_engagement_prompt_carries_the_decision_bullets(skill: str) -> None:
    """``engage`` IS the approval gate for outbound comments — the criteria
    for declining must reach the model or the gate degrades to always-engage."""
    prompt = _rendered(skill)
    for bullet in _DECISION_BULLETS:
        assert bullet in prompt, f"{skill}: missing decision bullet {bullet!r}"


@pytest.mark.parametrize("skill", _ENGAGEMENT_SKILLS)
def test_engagement_prompt_carries_its_own_length_rule(skill: str) -> None:
    """Python no longer restates a length rule in the user prompt, so each
    flow's skill must carry the one its entry point expects."""
    prompt = _rendered(skill)
    assert _LENGTH_RULES[skill] in prompt
    assert "If you decide to engage" in prompt


@pytest.mark.parametrize("skill", _ENGAGEMENT_SKILLS)
def test_engagement_prompt_names_the_right_surface(skill: str) -> None:
    """Regression guard for the byte-copied persona line: wp-comment-handler
    once told the model it was commenting on Instagram."""
    prompt = _rendered(skill)
    assert _FRAMING[skill] in prompt
    if skill not in ("ig-comment", "ig-engager"):
        assert "Instagram" not in prompt
