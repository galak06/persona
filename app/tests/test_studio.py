"""Tests for the studio pipeline: brand loading, planning, rendering."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from studio.brand import BrandProfile
from studio.images import generate_placeholder
from studio.plan import SLIDE_KEYS, plan_carousel
from studio.render import _slugify, render_carousel

DEMO_CONFIG = Path(__file__).resolve().parents[2] / "examples" / "demo-brand" / "config.json"

_MINIMAL_PLAN = {
    "caption": "a caption",
    "slides": [
        {
            "key": k,
            "headline": "HEAD",
            "subcopy": "sub",
            "image_query": "q",
            "image_brief": "b",
        }
        for k in ("hero", "info_1", "info_2", "final")
    ],
}


@pytest.fixture
def brand() -> BrandProfile:
    return BrandProfile.from_config(DEMO_CONFIG)


class TestBrandProfile:
    def test_loads_demo_config(self, brand: BrandProfile) -> None:
        assert brand.name == "Bean & Bloom"
        assert brand.niche
        assert brand.handle == "@beanandbloom"

    def test_handle_is_always_at_prefixed(self) -> None:
        b = BrandProfile("N", "", "", "", "", "", "", ig_handle="@already")
        assert b.handle == "@already"

    def test_blank_mascot_kind_never_guesses_a_species(self, brand: BrandProfile) -> None:
        """A brand that did not say what its mascot is must not have one invented."""
        assert brand.mascot_kind == ""
        assert brand.mascot_clause == "Pip, the brand's mascot"

    def test_named_mascot_kind_is_used_verbatim(self) -> None:
        b = BrandProfile("N", "", "", "Rex", "delivery van", "", "", "")
        assert b.mascot_clause == "Rex, the brand's delivery van"

    def test_no_mascot_yields_empty_clause(self) -> None:
        b = BrandProfile("N", "", "", "", "", "", "", "")
        assert b.mascot_clause == ""

    def test_cta_ribbon_strips_scheme(self) -> None:
        b = BrandProfile("N", "https://example.com/", "", "", "", "", "", "")
        assert b.cta_ribbon == "FULL GUIDE  →  EXAMPLE.COM"

    def test_missing_keys_fall_back(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({}), encoding="utf-8")
        assert BrandProfile.from_config(cfg).name == "Your Brand"


class TestOfflinePlan:
    def test_produces_all_slide_keys(self, brand: BrandProfile) -> None:
        plan = plan_carousel("cold brew basics", brand, offline=True)
        assert [s.key for s in plan.slides] == list(SLIDE_KEYS)

    def test_hero_headline_keeps_the_trailing_word(self, brand: BrandProfile) -> None:
        """A hook trimmed mid-phrase loses the word it turns on."""
        plan = plan_carousel("why your cold brew tastes bitter", brand, offline=True)
        assert plan.slides[0].headline.endswith("BITTER")

    def test_long_topic_is_bounded(self, brand: BrandProfile) -> None:
        plan = plan_carousel(" ".join(["word"] * 40), brand, offline=True)
        assert len(plan.slides[0].headline) <= 40

    def test_empty_topic_rejected(self, brand: BrandProfile) -> None:
        with pytest.raises(ValueError):
            plan_carousel("   ", brand, offline=True)

    def test_caption_is_never_empty(self, brand: BrandProfile) -> None:
        assert plan_carousel("x", brand, offline=True).caption.strip()


class TestPlaceholder:
    def test_is_deterministic(self) -> None:
        assert generate_placeholder("seed").bytes_ == generate_placeholder("seed").bytes_

    def test_varies_by_seed(self) -> None:
        assert generate_placeholder("a").bytes_ != generate_placeholder("b").bytes_

    def test_is_a_real_jpeg(self) -> None:
        assert generate_placeholder("seed").bytes_.startswith(b"\xff\xd8")


class TestSlugify:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Why Cold Brew?", "why-cold-brew"),
            ("  spaced  out  ", "spaced-out"),
            ("!!!", "carousel"),
        ],
    )
    def test_slugify(self, raw: str, expected: str) -> None:
        assert _slugify(raw) == expected


class TestRenderOffline:
    def test_renders_slides_and_caption(self, brand: BrandProfile, tmp_path: Path) -> None:
        result = render_carousel("cold brew basics", brand, tmp_path, offline=True, make_reel=False)
        assert len(result.slide_paths) == len(SLIDE_KEYS)
        assert all(p.is_file() and p.stat().st_size > 0 for p in result.slide_paths)
        assert result.caption_path is not None and result.caption_path.is_file()
        assert set(result.providers) == {"placeholder"}

    @pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
    def test_composes_a_reel(self, brand: BrandProfile, tmp_path: Path) -> None:
        result = render_carousel("cold brew", brand, tmp_path, offline=True, make_reel=True)
        assert result.reel_path is not None
        assert result.reel_path.stat().st_size > 0

    def test_reel_skipped_without_ffmpeg(
        self, brand: BrandProfile, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("studio.render.shutil.which", lambda _: None)
        result = render_carousel("cold brew", brand, tmp_path, offline=True, make_reel=True)
        assert result.reel_path is None
        assert "ffmpeg" in result.reel_skipped_reason


class TestCredentialHandling:
    def test_planner_sends_the_key_as_a_header_not_a_url_param(
        self, brand: BrandProfile, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A key in the query string lands in httpx's INFO log, and from there
        into any verbose output a user pastes into a bug report."""
        seen: dict[str, object] = {}

        class _Resp:
            status_code = 200

            @staticmethod
            def json() -> dict[str, Any]:
                return {
                    "candidates": [{"content": {"parts": [{"text": json.dumps(_MINIMAL_PLAN)}]}}]
                }

        def _fake_post(url: str, **kwargs: object) -> _Resp:
            seen["url"] = url
            seen["headers"] = kwargs.get("headers")
            seen["params"] = kwargs.get("params")
            return _Resp()

        monkeypatch.setenv("GEMINI_API_KEY", "SECRET-KEY-VALUE")
        monkeypatch.setattr("studio.plan.httpx.post", _fake_post)
        plan_carousel("cold brew", brand)

        assert "SECRET-KEY-VALUE" not in str(seen["url"])
        assert seen["params"] is None
        assert seen["headers"] == {"x-goog-api-key": "SECRET-KEY-VALUE"}

    def test_image_provider_sends_the_key_as_a_header(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, object] = {}

        class _Resp:
            status_code = 200

            @staticmethod
            def json() -> dict[str, Any]:
                return {"candidates": [{"content": {"parts": [{"inlineData": {"data": "//8="}}]}}]}

        def _fake_post(url: str, **kwargs: object) -> _Resp:
            seen["url"] = url
            seen["headers"] = kwargs.get("headers")
            return _Resp()

        monkeypatch.setenv("GEMINI_API_KEY", "SECRET-KEY-VALUE")
        monkeypatch.setattr("studio.images.httpx.post", _fake_post)
        from studio.images import _gemini_image

        _gemini_image("a cup of coffee")
        assert "SECRET-KEY-VALUE" not in str(seen["url"])
        assert seen["headers"] == {"x-goog-api-key": "SECRET-KEY-VALUE"}

    def test_planning_falls_back_offline_without_a_key(
        self, brand: BrandProfile, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        assert plan_carousel("cold brew", brand).slides
