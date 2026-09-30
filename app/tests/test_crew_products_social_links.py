"""`lib.crew.products.social_links` -- the per-channel affiliate link the owner
DMs after a "Comment KEYWORD" call-to-action.

The point of these tests is attribution. One post's product reaches Amazon
through three doors (the blog block, a Facebook DM, an Instagram DM) and the
only thing that tells them apart in a report is the `ascsubtag`. So the shape
is asserted against the BLOG link built from the same public helper: if either
side ever drifts, these assertions say so before a month of clicks lands in the
wrong bucket.

Pure functions -- no fakes, no I/O.
"""

from __future__ import annotations

import pytest

from lib.affiliate_resolver import build_affiliate_url
from lib.crew.products.block import blog_campaign_id
from lib.crew.products.social_links import (
    CHANNELS,
    Channel,
    social_campaign_id,
    spotlight_affiliate_url,
)

_ASIN = "B006W6YHHI"
_TAG = "dogfoodandfun-20"
_SLUG = "best-dental-chews-for-dogs"


def test_campaign_id_carries_the_channel_prefix_and_the_shared_slug() -> None:
    assert social_campaign_id("fb", _SLUG) == f"fb-{_SLUG}"
    assert social_campaign_id("ig", _SLUG) == f"ig-{_SLUG}"
    # The suffix is deliberately the SAME slug the blog placement uses, so all
    # three placements for a post line up under one slug in a report.
    assert blog_campaign_id(_SLUG) == f"blog-{_SLUG}"


@pytest.mark.parametrize("channel", ["fb", "ig"])
def test_url_is_the_blog_url_with_the_campaign_swapped(channel: Channel) -> None:
    blog_url = build_affiliate_url(_ASIN, _TAG, blog_campaign_id(_SLUG))
    social_url = spotlight_affiliate_url(_ASIN, _TAG, channel=channel, slug=_SLUG)

    assert social_url == blog_url.replace("=blog-", f"={channel}-")
    assert social_url == (
        f"https://www.amazon.com/dp/{_ASIN}?tag={_TAG}&ascsubtag={channel}-{_SLUG}"
    )
    # A raw `&`, never `&amp;`: this string is pasted into a DM, not HTML.
    assert "&amp;" not in social_url


def test_asin_and_tag_are_stripped_before_they_reach_the_url() -> None:
    url = spotlight_affiliate_url(f"  {_ASIN} ", f"\t{_TAG}\n", channel="fb", slug=_SLUG)
    assert url == f"https://www.amazon.com/dp/{_ASIN}?tag={_TAG}&ascsubtag=fb-{_SLUG}"


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_blank_slug_raises_rather_than_minting_a_dead_ascsubtag(blank: str) -> None:
    with pytest.raises(ValueError, match="slug"):
        social_campaign_id("fb", blank)
    with pytest.raises(ValueError, match="slug"):
        spotlight_affiliate_url(_ASIN, _TAG, channel="ig", slug=blank)


@pytest.mark.parametrize("channel", ["facebook", "FB", "instagram", "", "tiktok"])
def test_unknown_channel_raises_from_both_entry_points(channel: str) -> None:
    assert channel not in CHANNELS
    with pytest.raises(ValueError, match="channel"):
        social_campaign_id(channel, _SLUG)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="channel"):
        spotlight_affiliate_url(_ASIN, _TAG, channel=channel, slug=_SLUG)  # type: ignore[arg-type]


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_tag_raises_because_an_untagged_link_earns_nothing(blank: str) -> None:
    with pytest.raises(ValueError, match="tag"):
        spotlight_affiliate_url(_ASIN, blank, channel="fb", slug=_SLUG)


@pytest.mark.parametrize("blank", ["", "  "])
def test_blank_asin_raises(blank: str) -> None:
    with pytest.raises(ValueError, match="asin"):
        spotlight_affiliate_url(blank, _TAG, channel="ig", slug=_SLUG)
