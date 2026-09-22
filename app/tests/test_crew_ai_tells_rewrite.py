"""Tests for lib.crew.ai_tells.rewrite -- heading repair on published posts.

The LLM is always a stub: these assert the VALIDATION around the model, which
is the part that protects live content. A suggestion is only applied if it
survives the same scanner that flagged the original, and the patch must touch
heading text and nothing else -- an over-broad replace on a published post is
the failure mode that matters here, not a bad heading.
"""
# ruff: noqa: S101

from __future__ import annotations

from typing import Any

from lib.crew.ai_tells import scan_body
from lib.crew.ai_tells.rewrite import (
    HeadingRewrite,
    apply_heading_rewrites,
    propose_heading_rewrites,
    rewrites_as_json,
)
from lib.llm_client import LLMRequest


class _StubLLM:
    """Returns a canned payload and records the prompt it was given."""

    def __init__(self, payload: dict[str, Any] | None) -> None:
        self.payload = payload
        self.last_request: LLMRequest | None = None

    def complete(self, req: LLMRequest) -> str | None:  # pragma: no cover - unused
        return None

    def complete_json(
        self, req: LLMRequest, *, response_schema: dict[str, Any]
    ) -> dict[str, Any] | None:
        self.last_request = req
        return self.payload


_BODY = (
    "<h2>What I Learned About Bully Sticks</h2><p>She chewed through it in nine minutes.</p>"
    "<h2>FAQ</h2><p>Owners ask whether wipes replace brushing. They do not.</p>"
    "<h2>Our Pick</h2><p>I still buy the Whimzees, at $0.62 a chew.</p>"
)


# ── proposal validation ──────────────────────────────────────────────────────


def test_accepts_a_specific_replacement() -> None:
    llm = _StubLLM(
        {
            "headings": [
                {"old": "FAQ", "new": "What Owners Keep Asking Me"},
                {"old": "Our Pick", "new": "What I Still Buy"},
            ]
        }
    )
    rewrites = propose_heading_rewrites(
        title="Dental Chews", body_html=_BODY, flagged=["FAQ", "Our Pick"], llm=llm
    )
    assert [(r.old, r.new) for r in rewrites] == [
        ("FAQ", "What Owners Keep Asking Me"),
        ("Our Pick", "What I Still Buy"),
    ]


def test_rejects_a_suggestion_that_is_itself_a_template_label() -> None:
    """The model swapping "FAQ" for "Frequently Asked Questions" is the exact
    non-fix this validation exists to catch."""
    llm = _StubLLM({"headings": [{"old": "FAQ", "new": "Frequently Asked Questions"}]})
    assert propose_heading_rewrites(title="t", body_html=_BODY, flagged=["FAQ"], llm=llm) == []


def test_rejects_an_over_long_suggestion() -> None:
    llm = _StubLLM({"headings": [{"old": "FAQ", "new": "W" * 200}]})
    assert propose_heading_rewrites(title="t", body_html=_BODY, flagged=["FAQ"], llm=llm) == []


def test_rejects_an_unchanged_suggestion() -> None:
    llm = _StubLLM({"headings": [{"old": "FAQ", "new": "faq"}]})
    assert propose_heading_rewrites(title="t", body_html=_BODY, flagged=["FAQ"], llm=llm) == []


def test_ignores_a_heading_that_was_never_flagged() -> None:
    """The model must not be able to retitle a section nobody complained about."""
    llm = _StubLLM(
        {"headings": [{"old": "What I Learned About Bully Sticks", "new": "Something Else"}]}
    )
    assert propose_heading_rewrites(title="t", body_html=_BODY, flagged=["FAQ"], llm=llm) == []


def test_llm_failure_yields_no_rewrites() -> None:
    """A failed call must leave published content untouched, not half-edited."""
    assert (
        propose_heading_rewrites(title="t", body_html=_BODY, flagged=["FAQ"], llm=_StubLLM(None))
        == []
    )


def test_no_flagged_headings_makes_no_llm_call() -> None:
    llm = _StubLLM({"headings": []})
    assert propose_heading_rewrites(title="t", body_html=_BODY, flagged=[], llm=llm) == []
    assert llm.last_request is None


def test_prompt_carries_the_section_text_not_just_the_label() -> None:
    """The model retitles from what the section says; without the excerpt it
    can only paraphrase the template label it was asked to remove."""
    llm = _StubLLM({"headings": []})
    propose_heading_rewrites(title="Dental Chews", body_html=_BODY, flagged=["FAQ"], llm=llm)
    assert llm.last_request is not None
    assert "wipes replace brushing" in llm.last_request.user


# ── patching ─────────────────────────────────────────────────────────────────


def test_apply_replaces_only_the_heading_text() -> None:
    out = apply_heading_rewrites(_BODY, [HeadingRewrite(old="FAQ", new="What Owners Ask Me")])
    assert "<h2>What Owners Ask Me</h2>" in out
    assert "<h2>FAQ</h2>" not in out
    # Every other section survives byte-for-byte.
    assert "<p>She chewed through it in nine minutes.</p>" in out
    assert "<h2>Our Pick</h2>" in out


def test_apply_preserves_heading_attributes() -> None:
    body = '<h3 id="faq" class="wp-block-heading">FAQ</h3><p>x</p>'
    out = apply_heading_rewrites(body, [HeadingRewrite(old="FAQ", new="What Owners Ask")])
    assert out == '<h3 id="faq" class="wp-block-heading">What Owners Ask</h3><p>x</p>'


def test_apply_does_not_touch_matching_text_in_body_prose() -> None:
    """A bare string replace would corrupt the paragraph and the anchor."""
    body = '<h2>Our Pick</h2><p>Our Pick was the cheap one.</p><a href="/x">Our Pick</a>'
    out = apply_heading_rewrites(body, [HeadingRewrite(old="Our Pick", new="What I Buy")])
    assert out == '<h2>What I Buy</h2><p>Our Pick was the cheap one.</p><a href="/x">Our Pick</a>'


def test_apply_escapes_html_in_the_new_heading() -> None:
    out = apply_heading_rewrites(
        "<h2>FAQ</h2>", [HeadingRewrite(old="FAQ", new="Chews & Wipes <2026>")]
    )
    assert out == "<h2>Chews &amp; Wipes &lt;2026&gt;</h2>"


def test_rewriting_clears_the_finding_that_prompted_it() -> None:
    """End to end: the scan that flagged the post must pass after the patch."""
    post = f"<p>Your dog's breath clears a room.</p>{_BODY}"
    assert not scan_body(post).passed
    patched = apply_heading_rewrites(
        post,
        [
            HeadingRewrite(old="FAQ", new="What Owners Keep Asking Me"),
            HeadingRewrite(old="Our Pick", new="What I Still Buy"),
        ],
    )
    assert scan_body(patched).passed


def test_rewrites_as_json_records_both_sides() -> None:
    payload = rewrites_as_json([HeadingRewrite(old="FAQ", new="What Owners Ask")])
    assert payload == '[{"old": "FAQ", "new": "What Owners Ask"}]'


def test_duplicate_headings_get_distinct_replacements_in_order() -> None:
    """Live post 3294 carries "Frequently Asked Questions" twice. Each swap is
    `count=1`, so the first rewrite consumes the first occurrence and the
    second falls through to the next one -- both must land, in order, and
    neither may overwrite the other."""
    body = (
        "<h2>Frequently Asked Questions</h2><p>One.</p>"
        "<h2>Frequently Asked Questions</h2><p>Two.</p>"
    )
    out = apply_heading_rewrites(
        body,
        [
            HeadingRewrite(old="Frequently Asked Questions", new="Questions Engineers Ask Me"),
            HeadingRewrite(old="Frequently Asked Questions", new="Other Questions I Get"),
        ],
    )
    assert out == (
        "<h2>Questions Engineers Ask Me</h2><p>One.</p><h2>Other Questions I Get</h2><p>Two.</p>"
    )
