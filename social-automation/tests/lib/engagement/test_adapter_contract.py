"""Adapter Protocol contract tests.

Verifies that the three concrete adapter classes (Facebook, Instagram, Fake)
satisfy the OutboundAdapter Protocol and that FakeSource satisfies Source.

For the production adapters we only check class-level hasattr — they require
a real config dict + Playwright browser to construct. FakeAdapter is exercised
via isinstance because it has a no-arg-friendly constructor.
"""

from __future__ import annotations

from lib.engagement.adapter import OutboundAdapter, Source
from lib.engagement.adapters.facebook import FacebookGroupAdapter
from lib.engagement.adapters.fake import FakeAdapter, FakeSource
from lib.engagement.adapters.instagram import InstagramHashtagAdapter

REQUIRED_METHODS = (
    "session",
    "list_sources",
    "iterate_posts",
    "pre_filter",
    "adjust_score",
    "like",
)


def test_fake_adapter_satisfies_protocol() -> None:
    fa = FakeAdapter("instagram", [], {})
    assert isinstance(fa, OutboundAdapter)


def test_fake_adapter_facebook_construction_smoke() -> None:
    fa = FakeAdapter("facebook", [], {})
    assert fa.platform == "facebook"
    assert isinstance(fa, OutboundAdapter)


def test_facebook_adapter_has_protocol_methods() -> None:
    for m in REQUIRED_METHODS:
        assert hasattr(FacebookGroupAdapter, m), m
    assert FacebookGroupAdapter.platform == "facebook"


def test_instagram_adapter_has_protocol_methods() -> None:
    for m in REQUIRED_METHODS:
        assert hasattr(InstagramHashtagAdapter, m), m
    assert InstagramHashtagAdapter.platform == "instagram"


def test_fake_source_satisfies_protocol() -> None:
    s = FakeSource(id="x", name="x", url="https://x")
    assert isinstance(s, Source)
