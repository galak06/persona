"""Tests for reply_drafter — skill-backed prompts, fallbacks, relevance ranking.

Live Gemini calls are covered by manual smoke runs — these are fast unit
tests that exercise the non-network parts: the system/user prompt split
(system = the comment-composer skill's ``## LLM Prompt`` section, user =
per-call context + MODE + output contract), env-guard fallbacks, relevance
ranking, and voice-validation on templates.

The REAL ``app/.claude/skills/comment-composer/SKILL.md`` is loaded (so
drift in the machine-read section fails here), with brand vars stubbed and
all lru_caches cleared around each test per the skill_loader hygiene rules.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from lib import reply_drafter as rd
from lib import skill_loader
from lib.skill_loader import BrandVars

_BRAND = BrandVars(
    name="Acme Dogs",
    name_lower="acme dogs",
    domain="acmedogs.example",
    mascot="Rex",
    persona="Rex's Human",
)


@pytest.fixture(autouse=True)
def _no_gemini(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force fallback path — tests never hit the network. Every provider key
    goes, not just Gemini's, so lib.llm_client can't route to a live SDK."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOICE_PROVIDER", raising=False)


@pytest.fixture(autouse=True)
def _no_langfuse(monkeypatch: pytest.MonkeyPatch) -> None:
    """llm_tracing.trace_llm_call must no-op to a plain call() in tests."""
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)


@pytest.fixture(autouse=True)
def _skill_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Real comment-composer SKILL.md + stubbed brand vars; caches cleared
    before AND after so no rendered prompt leaks across tests/env."""
    monkeypatch.setattr(skill_loader, "load_brand_vars", lambda brand_dir=None: _BRAND)
    skill_loader.load_skill_prompt.cache_clear()
    rd._system_prompt.cache_clear()
    yield
    skill_loader.load_skill_prompt.cache_clear()
    rd._system_prompt.cache_clear()


def _capture_call(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Capture the (prompt, max_tokens, system) payload; return None so the
    caller takes its normal fallback path after capture."""
    seen: dict[str, Any] = {}

    def _fake(prompt: str, *, max_tokens: int, system: str | None = None) -> None:
        seen.update(prompt=prompt, max_tokens=max_tokens, system=system)
        return None

    monkeypatch.setattr(rd, "_llm_text", _fake)
    return seen


# ------------------------------------------------------- system/user split


def test_reply_sends_skill_section_as_system_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _capture_call(monkeypatch)
    rd.draft_reply(our_comment="c", their_reply="r", their_author="Alex Park", site_posts=[])
    system = seen["system"]
    assert system is not None
    assert "BRAND VOICE — authentic, warm, and specific to the brand persona:" in system
    assert "Additional rules for REPLIES specifically:" in system
    assert "Additional rules for FIRST-TOUCH COMMENTS:" in system
    assert "Rex's Human" in system  # {{brand.persona}} rendered
    assert "{{brand." not in system  # nothing survives unrendered


def test_reply_user_prompt_has_mode_context_contract_not_voice_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _capture_call(monkeypatch)
    rd.draft_reply(
        our_comment="our original comment",
        their_reply="their reply text",
        their_author="Alex Park",
        site_posts=[],
    )
    prompt = seen["prompt"]
    assert "MODE: reply" in prompt
    assert 'WHAT YOU ORIGINALLY COMMENTED:\n"our original comment"' in prompt
    assert 'WHAT THEY REPLIED (from Alex Park):\n"their reply text"' in prompt
    assert "THEIR FIRST NAME: Alex" in prompt
    assert "RELEVANT RECENT POSTS FROM YOUR SITE" in prompt
    assert "Output ONLY the reply text. No preamble, no quotes." in prompt
    assert "BRAND VOICE" not in prompt  # voice rules live in the system prompt only
    assert seen["max_tokens"] == 250


def test_comment_user_prompt_first_touch_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _capture_call(monkeypatch)
    rd.draft_comment(
        post_text="Anyone tried a topper?",
        category="food",
        group_or_hashtag="Homemade Dog Food",
        site_posts=[],
    )
    prompt = seen["prompt"]
    assert "MODE: first-touch" in prompt
    assert "WHERE YOU'RE COMMENTING: Homemade Dog Food (category: food)" in prompt
    assert 'THEIR POST (verbatim):\n"Anyone tried a topper?"' in prompt
    assert "Output ONLY the comment text. No preamble, no quotes." in prompt
    assert "BRAND VOICE" not in prompt
    assert seen["max_tokens"] == 300
    assert seen["system"] == rd._system_prompt()  # same skill prompt for both modes


# ------------------------------------------------------- fallback behaviour


def test_reply_falls_back_to_template_without_gemini() -> None:
    text = rd.draft_reply(
        our_comment="We tracked Nalla's shed volume for a month.",
        their_reply="How long before you saw a change?",
        their_author="Alex Park",
    )
    assert "Alex" in text
    assert text.rstrip().endswith("?")


def test_comment_returns_empty_string_without_gemini() -> None:
    # draft_comment has no conservative fallback — caller handles that.
    text = rd.draft_comment(
        post_text="Anyone have a good peanut butter recipe for dog treats?",
        category="food",
        group_or_hashtag="Homemade Dog Food Recipes",
    )
    assert text == ""


# --------------------------------------------------------- ranking + chrome


def test_relevant_posts_ranks_by_keyword_overlap() -> None:
    posts = [
        rd.SitePost(
            title="Spring Shedding Survival Guide",
            url="a",
            excerpt="",
            categories=["Grooming"],
            tags=["shedding", "deshedding"],
        ),
        rd.SitePost(
            title="Peanut Butter Banana Biscuits",
            url="b",
            excerpt="",
            categories=["Food"],
            tags=["recipes", "treats"],
        ),
        rd.SitePost(
            title="GPS Tracker Comparison",
            url="c",
            excerpt="",
            categories=["Gear"],
            tags=["gps", "tracker"],
        ),
    ]
    hits = rd._relevant_posts("Nalla has been shedding like crazy all spring", posts, limit=2)
    assert hits
    assert "Shedding" in hits[0].title


def test_relevant_posts_returns_empty_when_nothing_matches() -> None:
    posts = [
        rd.SitePost(
            title="GPS Tracker Comparison", url="c", excerpt="", categories=["Gear"], tags=["gps"]
        ),
    ]
    assert rd._relevant_posts("completely unrelated topic xyzzy", posts) == []


def test_strip_meta_chrome_removes_quotes_and_preamble() -> None:
    assert rd._strip_meta_chrome('"hello there"') == "hello there"
    assert rd._strip_meta_chrome("Reply: hello there") == "hello there"
    assert rd._strip_meta_chrome("Here is the reply: ok") == "ok"


# The transport moved out of this module: provider selection lives in
# lib/llm_client.py (tests/test_llm_client.py) and the Gemini HTTP calls in
# lib/gemini_client.py (tests/test_gemini_client.py).
