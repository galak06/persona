"""Tests for lib.crew.products.compliance -- the FTC/Associates surface.

The transformation these cover is shared by two callers: the drafting path
(`lib.crew.writer.assemble.assemble_final_html`) and the one-off repair of
an already-published post. Every case here is therefore written against the
pure function, plus one end-to-end assertion through `assemble_final_html`
so the wiring cannot rot independently of the function.

Shapes are taken from real posts: the `?tag=`-only href, the bare URL run
straight into an em-dash (post 4466), the generic "Affiliate disclosure"
line the writer emits, and the post with no Amazon link at all (post 4482).
No network, no WordPress, no DATABASE_URL.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib.affiliate_resolver import ProductEntry
from lib.crew.products.block import DISCLOSURE, render_blog_block
from lib.crew.products.compliance import enforce_affiliate_compliance, slug_for
from lib.crew.writer.models import FaqPair, WrittenPost
from lib.crew.writer.orchestrator import assemble_final_html

_SLUG = "human-grade-dog-food-worth-it-in-2026"
_CAMPAIGN = f"blog-{_SLUG}"
_TAG = "test-tag-20"
_GENERIC = "<p><em>Affiliate disclosure: Amazon affiliate links.</em></p>"


def _anchor(asin: str = "B0DJ9JVGBT", *, extra: str = "") -> str:
    return f'<a href="https://www.amazon.com/dp/{asin}?tag={_TAG}"{extra}>the topper</a>'


# ── slug_for ─────────────────────────────────────────────────────────────────


def test_slug_for_derives_a_wp_style_slug_from_the_title() -> None:
    assert slug_for("Human-Grade Dog Food: Worth It in 2026?") == _SLUG


def test_slug_for_never_returns_an_empty_campaign_id() -> None:
    """The draft trap: WP hands back `slug: ""`, and `ascsubtag=blog-` is a
    tag that attributes nothing (live on posts 4289 and 4295)."""
    assert slug_for("") == "post"
    assert slug_for("!!!") == "post"


# ── anchors ──────────────────────────────────────────────────────────────────


def test_an_amazon_anchor_gets_rel_target_and_the_campaign_id() -> None:
    out = enforce_affiliate_compliance(f"{_GENERIC}<p>{_anchor()}</p>", _SLUG)
    assert (
        f'href="https://www.amazon.com/dp/B0DJ9JVGBT?tag={_TAG}&amp;ascsubtag={_CAMPAIGN}"' in out
    )
    assert 'rel="sponsored nofollow"' in out
    assert 'target="_blank"' in out


def test_the_attribute_shape_matches_the_picks_block_byte_for_byte() -> None:
    """One convention, not two: a repaired anchor and a rendered one must be
    indistinguishable, or a reader (and Amazon) sees two kinds of link."""
    block = render_blog_block(
        [ProductEntry(key="k", asin="B0DJ9JVGBT", display="Topper")],
        _SLUG,
        associates_tag=_TAG,
    )
    rendered = block[block.index("<a href=") : block.index(">View on Amazon") + 1]
    repaired = enforce_affiliate_compliance(f"{_GENERIC}{_anchor()}", _SLUG)
    assert rendered in f"{repaired}>"


def test_a_url_with_no_query_string_gets_a_question_mark_separator() -> None:
    html = f'{_GENERIC}<a href="https://www.amazon.com/dp/B0DJ9JVGBT">x</a>'
    assert f'href="https://www.amazon.com/dp/B0DJ9JVGBT?ascsubtag={_CAMPAIGN}"' in (
        enforce_affiliate_compliance(html, _SLUG)
    )


def test_a_second_parameter_is_appended_after_an_existing_ampersand() -> None:
    html = f'{_GENERIC}<a href="https://www.amazon.com/dp/B0DJ9JVGBT?tag=t&amp;th=1">x</a>'
    assert f"?tag=t&amp;th=1&amp;ascsubtag={_CAMPAIGN}" in enforce_affiliate_compliance(html, _SLUG)


def test_the_campaign_id_goes_before_a_fragment_not_after_it() -> None:
    html = f'{_GENERIC}<a href="https://www.amazon.com/dp/B0DJ9JVGBT?tag=t#reviews">x</a>'
    assert f"?tag=t&amp;ascsubtag={_CAMPAIGN}#reviews" in enforce_affiliate_compliance(html, _SLUG)


def test_an_existing_rel_is_kept_and_never_duplicated() -> None:
    html = _GENERIC + _anchor(extra=' rel="nofollow"')
    out = enforce_affiliate_compliance(html, _SLUG)
    assert out.count("rel=") == 1
    assert 'rel="nofollow"' in out


def test_an_existing_target_is_kept_and_never_duplicated() -> None:
    html = _GENERIC + _anchor(extra=' target="_self"')
    out = enforce_affiliate_compliance(html, _SLUG)
    assert out.count("target=") == 1
    assert 'rel="sponsored nofollow"' in out


def test_internal_links_are_never_rewritten() -> None:
    """The rewrite keys on `amazon.com/dp/` in the href, so a same-site link
    cannot pick up `rel="sponsored"` -- which would tell Google the brand's
    own posts are paid placements."""
    internal = '<a href="https://example.com/recipes/beef-stew/">our beef stew</a>'
    out = enforce_affiliate_compliance(f"{_GENERIC}<p>{internal}</p>{_anchor()}", _SLUG)
    assert internal in out


# ── bare URLs ────────────────────────────────────────────────────────────────


def test_a_bare_amazon_url_in_prose_becomes_a_compliant_anchor() -> None:
    """Post 4466 shipped three of these. WordPress's `make_clickable` does
    not linkify them, so they were dead text earning nothing."""
    url = f"https://www.amazon.com/dp/B078TT6QYC?tag={_TAG}"
    out = enforce_affiliate_compliance(f"{_GENERIC}<p>We used {url} for months.</p>", _SLUG)
    assert (
        f'<a href="{url}&amp;ascsubtag={_CAMPAIGN}" rel="sponsored nofollow" '
        f'target="_blank">{url}</a>' in out
    )


def test_a_bare_url_run_into_an_em_dash_stops_at_the_url() -> None:
    url = f"https://www.amazon.com/dp/B0DJ9JVGBT?tag={_TAG}"
    out = enforce_affiliate_compliance(f"{_GENERIC}<p>the {url}—it had chicken.</p>", _SLUG)
    assert "</a>—it had chicken.</p>" in out
    assert "it had chicken</a>" not in out


def test_trailing_sentence_punctuation_stays_outside_the_link() -> None:
    url = "https://www.amazon.com/dp/B0DJ9JVGBT"
    out = enforce_affiliate_compliance(f"{_GENERIC}<p>We bought {url}.</p>", _SLUG)
    assert "</a>.</p>" in out


def test_a_url_already_inside_an_anchor_is_not_wrapped_again() -> None:
    url = f"https://www.amazon.com/dp/B0DJ9JVGBT?tag={_TAG}"
    out = enforce_affiliate_compliance(f'{_GENERIC}<a href="{url}">{url}</a>', _SLUG)
    assert out.count("<a ") == 1


def test_a_url_inside_a_script_tag_is_left_alone() -> None:
    """The JSON-LD tail is schema, not prose -- an `<a>` injected into it
    would be invalid JSON on the page."""
    script = (
        '<script type="application/ld+json">{"u":"https://www.amazon.com/dp/B0DJ9JVGBT"}</script>'
    )
    out = enforce_affiliate_compliance(f"{_GENERIC}{_anchor()}{script}", _SLUG)
    assert script in out


def test_bare_urls_can_be_left_alone_on_request() -> None:
    url = "https://www.amazon.com/dp/B0DJ9JVGBT"
    out = enforce_affiliate_compliance(
        f"{_GENERIC}<p>see {url}</p>{_anchor()}", _SLUG, link_bare_urls=False
    )
    assert f"<p>see {url}</p>" in out
    assert 'rel="sponsored nofollow"' in out


# ── the disclosure ───────────────────────────────────────────────────────────


def test_the_associates_statement_is_added_beside_the_generic_disclosure() -> None:
    """`_has_disclosure` is happy with the writer's generic line, which is
    exactly why the block's specific one got suppressed. The Operating
    Agreement wants this wording, so presence is tested on the wording."""
    out = enforce_affiliate_compliance(f"{_GENERIC}<p>{_anchor()}</p>", _SLUG)
    assert out.count(DISCLOSURE) == 1
    assert out.index(DISCLOSURE) < out.index("<a href=")


def test_the_statement_is_not_added_twice_when_a_picks_block_already_has_it() -> None:
    block = render_blog_block(
        [ProductEntry(key="k", asin="B0DJ9JVGBT", display="Topper")],
        _SLUG,
        associates_tag=_TAG,
    )
    assert enforce_affiliate_compliance(f"{_GENERIC}{block}", _SLUG).count(DISCLOSURE) == 1


def test_the_statement_lands_above_the_json_ld_tail() -> None:
    """`lib.crew.draft_body.split_body_and_jsonld` treats everything from the
    first schema `<script>` on as schema, so a disclosure appended after it
    would never reach the rendered body."""
    script = '<script type="application/ld+json">{"@type":"FAQPage"}</script>'
    out = enforce_affiliate_compliance(f"<div>{_anchor()}</div>\n\n{script}", _SLUG)
    assert out.index(DISCLOSURE) < out.index("<script")


def test_a_post_with_no_disclosure_at_all_gets_the_statement_appended() -> None:
    """`resolve_html` refuses a body with no disclosure, so the drafting path
    never reaches this -- a live post being repaired can."""
    out = enforce_affiliate_compliance(f"<p>Just prose and {_anchor()}</p>", _SLUG)
    assert out.count(DISCLOSURE) == 1
    assert out.rstrip().endswith("</em></p>")


def test_a_post_with_no_amazon_link_is_returned_unchanged() -> None:
    """Post 4482: an editorial post that carries a disclosure line for links
    it does not have. Adding a picks statement there is noise, not
    compliance."""
    html = f'{_GENERIC}<p><a href="https://example.com/x/">internal</a></p>'
    assert enforce_affiliate_compliance(html, _SLUG) == html


# ── idempotency ──────────────────────────────────────────────────────────────


def test_running_it_twice_equals_running_it_once() -> None:
    """The repair path and the drafting path both run this over the same
    post; neither may undo or double up on the other."""
    messy = (
        f"{_GENERIC}<p>{_anchor()}</p>"
        f"<p>also https://www.amazon.com/dp/B078TT6QYC?tag={_TAG} works</p>"
        '<p><a href="https://example.com/recipes/">ours</a></p>'
    )
    once = enforce_affiliate_compliance(messy, _SLUG)
    assert enforce_affiliate_compliance(once, _SLUG) == once
    assert enforce_affiliate_compliance(once, _SLUG).count(DISCLOSURE) == 1


# ── end to end through assemble_final_html ───────────────────────────────────


@pytest.fixture
def brand_dir(tmp_path: Path) -> Path:
    (tmp_path / "config.json").write_text(
        json.dumps({"site": {"name": "Brand", "url": "https://example.com"}}), encoding="utf-8"
    )
    return tmp_path


def _post(**overrides: Any) -> WrittenPost:
    defaults: dict[str, Any] = {
        "title": "Human-Grade Dog Food: Worth It in 2026?",
        "body_html": f"{_GENERIC}<p>Try [AFFILIATE:topper] this week.</p>",
        "word_count": 2600,
        "faq_pairs": [FaqPair(question="Is it worth it?", answer="Often.")],
        "affiliate_keys_used": ["topper"],
    }
    defaults.update(overrides)
    return WrittenPost(**defaults)


def test_an_assembled_draft_carries_the_campaign_id_rel_and_the_statement(
    brand_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole slice: a resolved placeholder comes out with `ascsubtag` at
    source, the anchor comes out `rel`/`target`-marked, and the Associates
    statement is on the post -- from a DRAFT, whose WP slug would be `""`."""
    monkeypatch.setenv("AMAZON_ASSOCIATES_TAG", _TAG)
    catalog = {"topper": ProductEntry(key="topper", asin="B0DJ9JVGBT", display="Topper")}

    final_html = assemble_final_html(brand_dir, _post(), catalog=catalog)

    assert f"ascsubtag={_CAMPAIGN}" in final_html
    assert 'rel="sponsored nofollow"' in final_html
    assert 'target="_blank"' in final_html
    assert final_html.count(DISCLOSURE) == 1
    assert 'ascsubtag=blog-"' not in final_html
