"""Tests for lib.crew.draft_body -- WordPress-presentation HTML composition.

Split out of lib.crew.draft purely for file-size discipline; these were
previously exercised only indirectly through create_wp_draft in
test_crew_draft_image.py -- this file gives them direct unit coverage too.
"""
# ruff: noqa: S101

from __future__ import annotations

from lib.crew.draft_body import (
    build_wrapped_body,
    derive_excerpt,
    escape_attr,
    slugify,
    split_body_and_jsonld,
)


def test_slugify_lowercases_and_hyphenates() -> None:
    assert slugify("A Great Post: 5 Tips!") == "a-great-post-5-tips"


def test_slugify_empty_title_falls_back_to_post() -> None:
    assert slugify("") == "post"
    assert slugify("!!!") == "post"


def test_escape_attr_escapes_html_special_chars() -> None:
    assert escape_attr('A "quoted" <tag> & more') == "A &quot;quoted&quot; &lt;tag&gt; &amp; more"


def test_split_body_and_jsonld_no_marker_returns_body_unchanged() -> None:
    assert split_body_and_jsonld("<p>no schema here</p>") == ("<p>no schema here</p>", "")


def test_split_body_and_jsonld_splits_at_script_marker() -> None:
    html = '<p>body</p>\n\n<script type="application/ld+json">{"a": 1}</script>\n'
    body, jsonld = split_body_and_jsonld(html)
    assert body == "<p>body</p>"
    assert jsonld == '<script type="application/ld+json">{"a": 1}</script>'


def test_build_wrapped_body_no_hero_no_jsonld() -> None:
    out = build_wrapped_body("<p>b</p>", media_source_url="", alt_text="")
    assert out == '<div class="dff-idea-body" style="max-width:720px;margin:0 auto;"><p>b</p></div>'


def test_build_wrapped_body_with_hero_figure() -> None:
    out = build_wrapped_body("<p>b</p>", media_source_url="https://x.com/img.png", alt_text="A dog")
    assert out == (
        '<div class="dff-idea-body" style="max-width:720px;margin:0 auto;">'
        '<figure><img src="https://x.com/img.png" alt="A dog" class="wp-post-image"></figure>'
        "<p>b</p></div>"
    )


def test_build_wrapped_body_keeps_jsonld_outside_wrapper_div() -> None:
    jsonld = '<script type="application/ld+json">{"a": 1}</script>'
    body_with_schema = f"<p>b</p>\n\n{jsonld}\n"
    out = build_wrapped_body(body_with_schema, media_source_url="", alt_text="")
    assert out == (
        f'<div class="dff-idea-body" style="max-width:720px;margin:0 auto;"><p>b</p></div>\n\n{jsonld}'
    )
    assert out.index("</div>") < out.index("<script")


def test_derive_excerpt_skips_byline_and_disclosure_paragraphs() -> None:
    html = (
        "<p>By Nalla's Dad / August 6, 2026</p>"
        "<p>Affiliate disclosure: This post contains Amazon affiliate links.</p>"
        "<p>Nalla wouldn't touch her bowl for two days straight.</p>"
    )
    assert derive_excerpt(html) == "Nalla wouldn't touch her bowl for two days straight."


def test_derive_excerpt_truncates_long_paragraph_at_word_boundary() -> None:
    long_text = "word " * 40
    html = f"<p>By Nalla's Dad / August 6, 2026</p><p>{long_text.strip()}</p>"
    excerpt = derive_excerpt(html, length=20)
    assert excerpt == "word word word word…"
    assert not excerpt[:-1].endswith("wor")  # never cuts mid-word


def test_derive_excerpt_strips_inner_tags_and_collapses_whitespace() -> None:
    html = "<p>By X / Y</p><p>Nalla  <strong>loved</strong>\n  this recipe.</p>"
    assert derive_excerpt(html) == "Nalla loved this recipe."


def test_derive_excerpt_ignores_jsonld_tail() -> None:
    html = '<p>By X / Y</p><script type="application/ld+json">{"a": "By nobody / nothing"}</script>'
    assert derive_excerpt(html) == ""


def test_derive_excerpt_returns_empty_when_only_boilerplate_present() -> None:
    html = "<p>By X / Y</p><p>Affiliate disclosure: links ahead.</p>"
    assert derive_excerpt(html) == ""
