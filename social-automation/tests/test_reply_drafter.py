"""Tests for reply_drafter — fallback paths + site-post relevance ranking.

Live Gemini calls are covered by manual smoke runs — these are fast unit
tests that exercise the non-network parts (relevance ranking, env-guard
fallbacks, voice-validation on templates).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

import reply_drafter as rd


@pytest.fixture(autouse=True)
def _no_gemini(monkeypatch):
    """Force fallback path — tests never hit the network."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


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


def test_relevant_posts_ranks_by_keyword_overlap() -> None:
    posts = [
        rd.SitePost(title="Spring Shedding Survival Guide", url="a", excerpt="",
                    categories=["Grooming"], tags=["shedding", "deshedding"]),
        rd.SitePost(title="Peanut Butter Banana Biscuits", url="b", excerpt="",
                    categories=["Food"], tags=["recipes", "treats"]),
        rd.SitePost(title="GPS Tracker Comparison", url="c", excerpt="",
                    categories=["Gear"], tags=["gps", "tracker"]),
    ]
    hits = rd._relevant_posts("Nalla has been shedding like crazy all spring", posts, limit=2)
    assert hits
    assert "Shedding" in hits[0].title


def test_relevant_posts_returns_empty_when_nothing_matches() -> None:
    posts = [
        rd.SitePost(title="GPS Tracker Comparison", url="c", excerpt="",
                    categories=["Gear"], tags=["gps"]),
    ]
    assert rd._relevant_posts("completely unrelated topic xyzzy", posts) == []


def test_strip_meta_chrome_removes_quotes_and_preamble() -> None:
    assert rd._strip_meta_chrome('"hello there"') == "hello there"
    assert rd._strip_meta_chrome("Reply: hello there") == "hello there"
    assert rd._strip_meta_chrome("Here is the reply: ok") == "ok"
