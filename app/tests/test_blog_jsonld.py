"""Tests for lib.blog_jsonld -- pure functions, no mocking needed."""
# ruff: noqa: S101

from __future__ import annotations

import json

from lib.blog_jsonld import article_jsonld, faq_jsonld, jsonld_script_block, render_jsonld_blocks


def test_article_jsonld_minimal_fields() -> None:
    schema = article_jsonld("My Title", "Nalla's Dad", "2026-08-06")
    assert schema["@context"] == "https://schema.org"
    assert schema["@type"] == "BlogPosting"
    assert schema["headline"] == "My Title"
    assert schema["author"] == {"@type": "Person", "name": "Nalla's Dad"}
    assert schema["datePublished"] == "2026-08-06"
    assert schema["dateModified"] == "2026-08-06"  # defaults to datePublished
    assert "description" not in schema
    assert "image" not in schema
    assert "publisher" not in schema


def test_article_jsonld_full_fields() -> None:
    schema = article_jsonld(
        "My Title",
        "Nalla's Dad",
        "2026-08-06",
        description="A description",
        image_url="https://example.com/img.png",
        publisher_name="DogFoodAndFun",
        publisher_url="https://dogfoodandfun.com",
        date_modified="2026-08-07",
        article_type="Article",
    )
    assert schema["@type"] == "Article"
    assert schema["description"] == "A description"
    assert schema["image"] == ["https://example.com/img.png"]
    assert schema["publisher"] == {
        "@type": "Organization",
        "name": "DogFoodAndFun",
        "url": "https://dogfoodandfun.com",
    }
    assert schema["dateModified"] == "2026-08-07"


def test_article_jsonld_publisher_without_url() -> None:
    schema = article_jsonld("T", "A", "2026-01-01", publisher_name="Pub")
    assert schema["publisher"] == {"@type": "Organization", "name": "Pub"}


def test_faq_jsonld_empty_returns_none() -> None:
    assert faq_jsonld([]) is None


def test_faq_jsonld_all_blank_returns_none() -> None:
    assert faq_jsonld([("", ""), ("  ", "answer"), ("question", "  ")]) is None


def test_faq_jsonld_builds_main_entity() -> None:
    schema = faq_jsonld([("Q1?", "A1."), ("Q2?", "A2.")])
    assert schema is not None
    assert schema["@type"] == "FAQPage"
    assert schema["mainEntity"] == [
        {"@type": "Question", "name": "Q1?", "acceptedAnswer": {"@type": "Answer", "text": "A1."}},
        {"@type": "Question", "name": "Q2?", "acceptedAnswer": {"@type": "Answer", "text": "A2."}},
    ]


def test_faq_jsonld_strips_whitespace_and_drops_blank_pairs() -> None:
    schema = faq_jsonld([("  Q1?  ", "  A1.  "), ("", "A2."), ("Q3?", "")])
    assert schema is not None
    assert len(schema["mainEntity"]) == 1
    assert schema["mainEntity"][0]["name"] == "Q1?"
    assert schema["mainEntity"][0]["acceptedAnswer"]["text"] == "A1."


def test_jsonld_script_block_renders_valid_json() -> None:
    block = jsonld_script_block({"@type": "Thing", "name": "x"})
    assert block.startswith('<script type="application/ld+json">')
    assert block.endswith("</script>")
    inner = block[len('<script type="application/ld+json">') : -len("</script>")]
    assert json.loads(inner) == {"@type": "Thing", "name": "x"}


def test_render_jsonld_blocks_skips_none() -> None:
    article = article_jsonld("T", "A", "2026-01-01")
    out = render_jsonld_blocks(article, None, None)
    assert out.count("<script") == 1
    assert "BlogPosting" in out


def test_render_jsonld_blocks_combines_multiple() -> None:
    article = article_jsonld("T", "A", "2026-01-01")
    faq = faq_jsonld([("Q?", "A.")])
    out = render_jsonld_blocks(article, faq)
    assert out.count("<script") == 2
    assert "BlogPosting" in out
    assert "FAQPage" in out


def test_render_jsonld_blocks_all_none_returns_empty_string() -> None:
    assert render_jsonld_blocks(None, None) == ""
