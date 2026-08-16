"""Tests for affiliate_resolver.py — placeholder → Amazon URL resolution + guards."""

from __future__ import annotations

import pytest

from lib import affiliate_resolver as ar


@pytest.fixture
def catalog() -> dict[str, ar.ProductEntry]:
    return {
        "nom-nom-fresh": ar.ProductEntry(
            key="nom-nom-fresh", asin="B07XQXZXJR", display="Nom Nom Fresh"
        ),
        "fi-collar": ar.ProductEntry(
            key="fi-collar", asin="B0B3Y9N1Z7", display="Fi Series 3 GPS Collar"
        ),
    }


_DISCLOSURE_BLOCK = (
    "<p><em>Affiliate disclosure: this post contains Amazon affiliate links. "
    "Purchases support the site at no extra cost to you.</em></p>"
)


def _body(inner: str) -> str:
    return _DISCLOSURE_BLOCK + inner


def test_resolves_known_placeholder(catalog) -> None:
    html = _body('<a href="[AFFILIATE:nom-nom-fresh]">Nom Nom</a>')
    out = ar.resolve_html(html, associates_tag="nallasdad-20", catalog=catalog)
    assert "[AFFILIATE:" not in out
    assert "amazon.com/dp/B07XQXZXJR?tag=nallasdad-20" in out


def test_resolves_multiple_and_mixed_case(catalog) -> None:
    html = _body("Best: [AFFILIATE:Nom-Nom-Fresh] · Runner up: [AFFILIATE:fi-collar]")
    out = ar.resolve_html(html, associates_tag="x-20", catalog=catalog)
    assert out.count("tag=x-20") == 2
    assert "B07XQXZXJR" in out and "B0B3Y9N1Z7" in out


def test_unknown_placeholder_raises(catalog) -> None:
    html = _body("[AFFILIATE:unknown-thing]")
    with pytest.raises(ar.AffiliateResolverError, match="unknown product"):
        ar.resolve_html(html, associates_tag="x-20", catalog=catalog)


# ── drop_unknown: an invented key costs a link, not the whole draft ─────────
#
# Live case (2026-08-16): the writer emitted four placeholders, three valid and
# one invented ('plentum-gut-health-powder'), and the fail-closed resolve threw
# away a complete 1654-word post. These pin the salvage path.


def test_drop_unknown_keeps_post_and_resolves_the_valid_keys(catalog) -> None:
    html = _body(
        '<a href="[AFFILIATE:nom-nom-fresh]">Nom Nom</a>'
        '<a href="[AFFILIATE:plentum-gut-health-powder]">Plentum Powder</a>'
    )
    out = ar.resolve_html(html, associates_tag="x-20", catalog=catalog, drop_unknown=True)
    assert "amazon.com/dp/B07XQXZXJR?tag=x-20" in out  # valid key still resolved
    assert "[AFFILIATE:" not in out  # no placeholder survives
    assert "plentum-gut-health-powder" not in out  # the invented KEY is gone
    # The anchor's visible TEXT survives by design (unwrapping, not deletion):
    # cutting it would gut a table cell or a mid-sentence product name. The
    # cost is that the post can still NAME a product it no longer links --
    # deliberate, and caught downstream by the quality editor plus the
    # human WordPress-draft approval gate.
    assert "Plentum Powder" in out


def test_drop_unknown_unwraps_the_anchor_instead_of_leaving_a_dead_link(catalog) -> None:
    # Deleting only the placeholder would leave <a href="">, which is worse
    # than no link at all -- the anchor must be unwrapped to its own text.
    html = _body('<p>Try <a href="[AFFILIATE:made-up-key]">this topper</a> next.</p>')
    out = ar.resolve_html(html, associates_tag="x-20", catalog=catalog, drop_unknown=True)
    assert "this topper" in out  # prose preserved
    assert 'href=""' not in out
    assert "<a" not in out.split(_DISCLOSURE_BLOCK)[1]


def test_drop_unknown_handles_a_bare_placeholder_outside_an_anchor(catalog) -> None:
    html = _body("<p>Our pick: [AFFILIATE:made-up-key] is great.</p>")
    out = ar.resolve_html(html, associates_tag="x-20", catalog=catalog, drop_unknown=True)
    assert "[AFFILIATE:" not in out
    assert "Our pick:" in out and "is great." in out


def test_drop_unknown_still_raises_on_missing_disclosure(catalog) -> None:
    # Dropping is about invented keys only. The FTC-disclosure and missing-tag
    # guards produce a legally wrong post, not a thinner one, so they must
    # still abort even in salvage mode.
    with pytest.raises(ar.AffiliateResolverError, match="disclosure"):
        ar.resolve_html(
            "<p>[AFFILIATE:nom-nom-fresh]</p>",
            associates_tag="x-20",
            catalog=catalog,
            drop_unknown=True,
        )


def test_drop_unknown_defaults_off_so_existing_callers_still_fail_closed(catalog) -> None:
    with pytest.raises(ar.AffiliateResolverError, match="unknown product"):
        ar.resolve_html(_body("[AFFILIATE:unknown-thing]"), associates_tag="x-20", catalog=catalog)


def test_missing_tag_raises(catalog, monkeypatch) -> None:
    monkeypatch.delenv("AMAZON_ASSOCIATES_TAG", raising=False)
    with pytest.raises(ar.AffiliateResolverError, match="AMAZON_ASSOCIATES_TAG"):
        ar.resolve_html(_body("[AFFILIATE:nom-nom-fresh]"), catalog=catalog)


def test_tag_from_env_when_not_passed(catalog, monkeypatch) -> None:
    monkeypatch.setenv("AMAZON_ASSOCIATES_TAG", "envtag-20")
    out = ar.resolve_html(_body("[AFFILIATE:nom-nom-fresh]"), catalog=catalog)
    assert "tag=envtag-20" in out


def test_missing_disclosure_raises(catalog) -> None:
    html = "<p>No disclosure here [AFFILIATE:nom-nom-fresh]</p>"
    with pytest.raises(ar.AffiliateResolverError, match="disclosure"):
        ar.resolve_html(html, associates_tag="x-20", catalog=catalog)


def test_no_placeholders_is_passthrough(catalog) -> None:
    html = _body("<p>No affiliate links here.</p>")
    assert ar.resolve_html(html, associates_tag="x-20", catalog=catalog) == html


def test_campaign_id_added_as_subtag(catalog) -> None:
    out = ar.resolve_html(
        _body("[AFFILIATE:nom-nom-fresh]"),
        associates_tag="x-20",
        campaign_id="2026-04-nom-nom-fresh",
        catalog=catalog,
    )
    assert "ascsubtag=2026-04-nom-nom-fresh" in out


def test_build_url_shape() -> None:
    assert ar.build_affiliate_url("B07XQXZXJR", "nallasdad-20") == (
        "https://www.amazon.com/dp/B07XQXZXJR?tag=nallasdad-20"
    )
    assert ar.build_affiliate_url("ASIN", "t", campaign_id="c1").endswith("ascsubtag=c1")


def test_lookup_unknown_key_raises(catalog) -> None:
    with pytest.raises(ar.AffiliateResolverError, match="unknown product"):
        ar.lookup("does-not-exist", catalog=catalog)
