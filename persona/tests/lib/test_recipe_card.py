# pyright: reportMissingImports=false
# ruff: noqa: S101
"""Tests for lib.recipe_card — content_parser, pdf_generator, and wp_sync.

Covers:
  - content_parser: happy path, missing headings, broken HTML, edge cases
  - pdf_generator: PDF bytes validation, stamp failure tolerance, layout variants
  - wp_sync: idempotency check, fetch_post_data validation, upload_pdf validation
"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from lib.recipe_card.content_parser import RecipeData, parse_recipe

# ---------------------------------------------------------------------------
# content_parser
# ---------------------------------------------------------------------------


class TestParseRecipe:
    def test_happy_path_extracts_ingredients_and_instructions(self) -> None:
        html = """
        <h2>Ingredients</h2>
        <ul>
          <li>2 cups oat flour</li>
          <li>1 egg</li>
          <li>1/4 cup peanut butter</li>
        </ul>
        <h2>Instructions</h2>
        <ol>
          <li>Preheat oven to 350°F</li>
          <li>Mix all ingredients</li>
          <li>Bake for 25 minutes</li>
        </ol>
        """
        result = parse_recipe("Turkey Jerky", html)
        assert result.title == "Turkey Jerky"
        assert result.ingredients == ["2 cups oat flour", "1 egg", "1/4 cup peanut butter"]
        assert result.instructions == [
            "Preheat oven to 350°F",
            "Mix all ingredients",
            "Bake for 25 minutes",
        ]

    def test_extracts_cook_temp_from_text(self) -> None:
        html = "<h2>Instructions</h2><ul><li>Bake at 350°F until done</li></ul>"
        result = parse_recipe("Recipe", html)
        assert result.cook_temp == "350°F"

    def test_extracts_cook_time_minutes(self) -> None:
        html = "<h2>Instructions</h2><ul><li>Cook for 25 minutes</li></ul>"
        result = parse_recipe("Recipe", html)
        assert result.cook_time == "25 minutes"

    def test_extracts_cook_time_hours(self) -> None:
        html = "<h2>Instructions</h2><ul><li>Dehydrate for 4 hours at 200°F</li></ul>"
        result = parse_recipe("Recipe", html)
        assert result.cook_time == "4 hours"
        assert result.cook_temp == "200°F"

    def test_missing_headings_returns_empty_lists(self) -> None:
        result = parse_recipe("No Structure", "<p>Just some plain text here.</p>")
        assert isinstance(result, RecipeData)
        assert result.ingredients == []
        assert result.instructions == []

    def test_empty_html_does_not_raise(self) -> None:
        result = parse_recipe("Empty", "")
        assert result.ingredients == []
        assert result.instructions == []

    def test_broken_html_does_not_raise(self) -> None:
        result = parse_recipe("Broken", "<h2>Ingredients</h2><ul><li>item1</ul><<<<")
        assert isinstance(result, RecipeData)

    def test_unknown_heading_resets_active_section(self) -> None:
        html = """
        <h2>Ingredients</h2>
        <ul><li>flour</li></ul>
        <h2>Notes</h2>
        <ul><li>should not be captured</li></ul>
        """
        result = parse_recipe("Reset", html)
        assert result.ingredients == ["flour"]
        assert "should not be captured" not in result.ingredients
        assert "should not be captured" not in result.instructions

    def test_instruction_heading_variants(self) -> None:
        for heading in ("Directions", "Steps", "How to Make"):
            html = f"<h2>{heading}</h2><ol><li>Step one</li></ol>"
            result = parse_recipe("R", html)
            assert result.instructions == ["Step one"], f"Failed for heading: {heading!r}"

    def test_returns_recipe_data_dataclass(self) -> None:
        result = parse_recipe("T", "")
        assert isinstance(result, RecipeData)

    def test_title_preserved(self) -> None:
        result = parse_recipe("My Special Recipe", "")
        assert result.title == "My Special Recipe"

    def test_cook_time_singular_minute(self) -> None:
        html = "<p>Bake for 1 minute only</p>"
        result = parse_recipe("R", html)
        assert result.cook_time == "1 minute"

    def test_cook_time_singular_hour(self) -> None:
        html = "<p>Dehydrate for 1 hour</p>"
        result = parse_recipe("R", html)
        assert result.cook_time == "1 hour"

    def test_no_temp_when_absent(self) -> None:
        result = parse_recipe("R", "<p>No temperature mentioned</p>")
        assert result.cook_temp == ""

    def test_ingredients_h3_heading(self) -> None:
        html = "<h3>Ingredients</h3><ul><li>item A</li></ul>"
        result = parse_recipe("R", html)
        assert result.ingredients == ["item A"]


# ---------------------------------------------------------------------------
# pdf_generator
# ---------------------------------------------------------------------------

from lib.recipe_card.pdf_generator import generate_recipe_card_pdf  # noqa: E402


class TestGenerateRecipeCardPdf:
    def test_returns_valid_pdf_bytes(self) -> None:
        pdf = generate_recipe_card_pdf("Title", ["item"], ["step"], b"")
        assert isinstance(pdf, bytes)
        assert pdf[:4] == b"%PDF"
        assert len(pdf) > 500

    def test_empty_ingredients_and_instructions_still_renders(self) -> None:
        pdf = generate_recipe_card_pdf("Minimal", [], [], b"")
        assert pdf[:4] == b"%PDF"

    def test_invalid_stamp_bytes_does_not_crash(self) -> None:
        pdf = generate_recipe_card_pdf("T", ["i"], ["s"], b"notanimage")
        assert pdf[:4] == b"%PDF"

    def test_empty_stamp_bytes_does_not_crash(self) -> None:
        pdf = generate_recipe_card_pdf("T", ["i"], ["s"], b"")
        assert pdf[:4] == b"%PDF"

    def test_two_col_path_eight_ingredients(self) -> None:
        items = [f"item {i}" for i in range(8)]
        pdf = generate_recipe_card_pdf("T", items, ["step"], b"")
        assert pdf[:4] == b"%PDF"

    def test_single_col_path_nine_ingredients(self) -> None:
        items = [f"item {i}" for i in range(9)]
        pdf = generate_recipe_card_pdf("T", items, ["step"], b"")
        assert pdf[:4] == b"%PDF"

    def test_cook_temp_and_time_present(self) -> None:
        pdf = generate_recipe_card_pdf(
            "T", ["i"], ["s"], b"", cook_temp="350°F", cook_time="25 minutes"
        )
        assert pdf[:4] == b"%PDF"

    def test_no_cook_info_does_not_crash(self) -> None:
        pdf = generate_recipe_card_pdf("T", ["i"], ["s"], b"", cook_temp="", cook_time="")
        assert pdf[:4] == b"%PDF"

    def test_long_ingredient_text_wraps(self) -> None:
        long_item = "a very long ingredient name that triggers word wrap in the PDF layout engine"
        pdf = generate_recipe_card_pdf("T", [long_item], ["step"], b"")
        assert pdf[:4] == b"%PDF"

    def test_many_instructions_do_not_overflow(self) -> None:
        steps = [f"Step {i}: do something specific here" for i in range(20)]
        pdf = generate_recipe_card_pdf("T", ["ing"], steps, b"")
        assert pdf[:4] == b"%PDF"

    def test_valid_pillow_stamp_renders(self) -> None:
        from PIL import Image  # type: ignore[import-untyped]

        img = Image.new("RGBA", (200, 200), color=(255, 0, 0, 128))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        stamp_bytes = buf.getvalue()
        pdf = generate_recipe_card_pdf("T", ["i"], ["s"], stamp_bytes)
        assert pdf[:4] == b"%PDF"


# ---------------------------------------------------------------------------
# wp_sync — unit-level (no live HTTP)
# ---------------------------------------------------------------------------

from lib.recipe_card.wp_sync import (  # noqa: E402
    _DOWNLOAD_BUTTON_MARKER,
    _NALLA_STAMP_MEDIA_ID,
    fetch_post_data,
    inject_download_button,
    upload_pdf,
)


class TestWpSyncConstants:
    def test_nalla_stamp_media_id(self) -> None:
        assert _NALLA_STAMP_MEDIA_ID == 3717

    def test_download_button_marker(self) -> None:
        assert _DOWNLOAD_BUTTON_MARKER == "recipe-card-download"


class TestInjectDownloadButtonIdempotency:
    """inject_download_button must not call the WP API when marker already present."""

    def _make_post_data(self, has_marker: bool) -> dict:
        content = (
            f'<p>Content here</p>\n<div class="{_DOWNLOAD_BUTTON_MARKER}">button</div>'
            if has_marker
            else "<p>Content here</p>"
        )
        return {"title": "Test Post", "content": content, "slug": "test-post"}

    def test_skips_when_marker_present(self) -> None:
        post_data = self._make_post_data(has_marker=True)
        with (
            patch("lib.recipe_card.wp_sync.fetch_post_data", return_value=post_data),
            patch("lib.recipe_card.wp_sync.wp_client") as mock_client,
        ):
            result = inject_download_button(123, "https://example.com/card.pdf")
        assert result is False
        mock_client.assert_not_called()

    def test_injects_when_marker_absent(self) -> None:
        post_data = self._make_post_data(has_marker=False)
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.patch.return_value = mock_resp

        with (
            patch("lib.recipe_card.wp_sync.fetch_post_data", return_value=post_data),
            patch("lib.recipe_card.wp_sync.wp_client", return_value=mock_client),
        ):
            result = inject_download_button(123, "https://example.com/card.pdf")
        assert result is True

    def test_injected_content_contains_marker_and_url(self) -> None:
        post_data = self._make_post_data(has_marker=False)
        captured_payload: list[dict] = []
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None

        def fake_patch(path: str, **kwargs: object) -> MagicMock:
            captured_payload.append(kwargs.get("json", {}))  # type: ignore[arg-type]
            return mock_resp

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.patch.side_effect = fake_patch

        with (
            patch("lib.recipe_card.wp_sync.fetch_post_data", return_value=post_data),
            patch("lib.recipe_card.wp_sync.wp_client", return_value=mock_client),
        ):
            inject_download_button(123, "https://example.com/card.pdf")

        assert captured_payload, "PATCH was never called"
        sent_content: str = captured_payload[0]["content"]
        assert _DOWNLOAD_BUTTON_MARKER in sent_content
        assert "https://example.com/card.pdf" in sent_content


class TestFetchPostDataValidation:
    def test_raises_on_non_published_status(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "status": "draft",
            "title": {"rendered": "T"},
            "content": {"rendered": "<p>C</p>"},
            "slug": "t",
        }
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp

        with (
            patch("lib.recipe_card.wp_sync.wp_client", return_value=mock_client),
            pytest.raises(ValueError, match="not published"),
        ):
            fetch_post_data(999)

    def test_raises_on_404(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.raise_for_status.return_value = None
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp

        with (
            patch("lib.recipe_card.wp_sync.wp_client", return_value=mock_client),
            pytest.raises(ValueError, match="not found"),
        ):
            fetch_post_data(999)

    def test_returns_title_content_slug_on_success(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "status": "publish",
            "title": {"rendered": "My Recipe"},
            "content": {"rendered": "<p>Body</p>"},
            "slug": "my-recipe",
        }
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = mock_resp

        with patch("lib.recipe_card.wp_sync.wp_client", return_value=mock_client):
            result = fetch_post_data(42)
        assert result == {"title": "My Recipe", "content": "<p>Body</p>", "slug": "my-recipe"}


class TestUploadPdf:
    def test_raises_when_source_url_missing(self) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {}
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp

        with (
            patch("lib.recipe_card.wp_sync.wp_client", return_value=mock_client),
            pytest.raises(ValueError, match="source_url"),
        ):
            upload_pdf(b"%PDF-1.4", "card.pdf")

    def test_returns_source_url_on_success(self) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"source_url": "https://cdn.example.com/card.pdf"}
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp

        with patch("lib.recipe_card.wp_sync.wp_client", return_value=mock_client):
            url = upload_pdf(b"%PDF-1.4", "card.pdf")
        assert url == "https://cdn.example.com/card.pdf"
