"""End-to-end tests for `scripts/backfill_blog_product_blocks.main()`.

Everything is faked at a real seam: `respx` intercepts the WordPress REST
API, and the selector's `SelectorExecuteFn` seam (the same one
tests/test_crew_products_select.py uses) returns canned `ProductSelection`
objects. No network, no DeepSeek, no DB -- and the two env loaders are
stubbed out so a developer's real settings.local.json is never read into
os.environ mid-suite.

The assertions that matter most are the negative ones: an Elementor post,
a recipe post, a failed selector and a dry run must each issue ZERO POSTs.
The module-level pieces (brief synthesis, guards, renderers, selection)
are unit-tested in tests/test_crew_products_blog_backfill.py.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import respx
from httpx import Request, Response
from scripts import backfill_blog_product_blocks as backfill

from lib.affiliate_resolver import ProductEntry
from lib.crew.products.blog_backfill import (
    WpPost,
    render_for_mode,
)
from lib.crew.products.models import ProductSelection, SelectedProduct
from lib.recipe_products import block_renderer as recipe_block

_POSTS_URL = "https://wp.test/wp-json/wp/v2/posts"

_CATALOG = [
    {
        "key": "gps-tracker",
        "asin": "B000000001",
        "display": "PawTrack GPS Collar",
        "category": "gps-tracker",
        "notes": "no-subscription tracker",
    },
    {
        "key": "run-harness",
        "asin": "B000000002",
        "display": "TrailBlazer Harness",
        "category": "gear",
        "notes": "chest-clip harness",
    },
    {
        "key": "cooling-vest",
        "asin": "B000000003",
        "display": "ChillPup Cooling Vest",
        "category": "cooling-gear",
        "notes": "evaporative vest",
    },
]

_BODY = (
    "<p>Nalla lost her collar on a trail, so I tested trackers.</p>\n"
    "<h2>How GPS trackers work</h2>\n"
    "<p>Cellular trackers ping a tower every few minutes &amp; drain battery.</p>\n"
    '<h2 class="wp-block-heading">FAQ</h2>\n<h3>Do they need a subscription?</h3>'
)


def _entry(key: str) -> ProductEntry:
    row = next(item for item in _CATALOG if item["key"] == key)
    return ProductEntry(
        key=row["key"], asin=row["asin"], display=row["display"], notes=row["notes"]
    )


def _row(**over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": 101,
        "slug": "dog-gps-guide",
        "title": {"raw": "Best Dog GPS Trackers &#8212; 2026"},
        "content": {"raw": _BODY},
        "meta": {},
    }
    row.update(over)
    return row


def _posts_response(rows: list[dict[str, Any]], *, total_pages: int = 1) -> Response:
    return Response(200, json=rows, headers={"X-WP-TotalPages": str(total_pages)})


def _fake_execute(*keys: str, seen: list[str] | None = None):
    """A `SelectorExecuteFn` returning a canned pick, recording each prompt."""

    def _execute(_agent: Any, task: Any) -> ProductSelection:
        if seen is not None:
            seen.append(task.description)
        return ProductSelection(
            products=[SelectedProduct(key=key, reason="fits this post") for key in keys]
        )

    return _execute


@pytest.fixture
def brand_dir(tmp_path: Path) -> Path:
    config = tmp_path / "brand" / "data" / "config"
    config.mkdir(parents=True)
    (config / "affiliate_products.json").write_text(json.dumps(_CATALOG), encoding="utf-8")
    return tmp_path / "brand"


@pytest.fixture
def wp_env(monkeypatch: pytest.MonkeyPatch, brand_dir: Path) -> Path:
    monkeypatch.setenv("WP_URL", "https://wp.test")
    monkeypatch.setenv("WP_USER", "editor")
    monkeypatch.setenv("WP_APP_PASSWORD", "app-password")
    monkeypatch.setenv("AMAZON_ASSOCIATES_TAG", "dogfoodandfun-20")
    monkeypatch.setenv("BRAND_DIR", str(brand_dir))
    monkeypatch.setattr(backfill, "load_local_env", lambda *_a, **_k: 0)
    monkeypatch.setattr(backfill, "load_brand_env_into_environ", lambda *_a, **_k: 0)
    return brand_dir


class TestBackfillRun:
    @respx.mock
    def test_insert_writes_backup_before_posting(self, wp_env: Path, tmp_path: Path, capsys):
        backup_root = tmp_path / "backups"
        respx.get(_POSTS_URL).mock(return_value=_posts_response([_row()]))

        def _assert_backup_first(request: Request) -> Response:
            saved = list(backup_root.glob("*/101-dog-gps-guide.html"))
            assert saved, "backup must be written BEFORE the post is updated"
            assert saved[0].read_text(encoding="utf-8") == _BODY
            return Response(200, json={"id": 101})

        update = respx.post(f"{_POSTS_URL}/101").mock(side_effect=_assert_backup_first)

        rc = backfill.main(
            ["--backup-dir", str(backup_root)], execute_fn=_fake_execute("gps-tracker")
        )

        assert rc == 0
        assert update.called
        payload = json.loads(update.calls.last.request.read())
        assert "status" not in payload, "publish state must never be touched"
        assert payload["meta"]["_elementor_data"] == ""
        assert "<!-- blog-picks-block:v1 -->" in payload["content"]
        assert "PawTrack GPS Collar" in payload["content"]
        # Inserted before the FAQ, not appended after it.
        assert payload["content"].index("blog-picks-block") < payload["content"].index(">FAQ<")
        assert "inserted=1" in capsys.readouterr().out
        usage = wp_env / "state" / "product_usage.jsonl"
        assert json.loads(usage.read_text(encoding="utf-8"))["keys"] == ["gps-tracker"]

    @respx.mock
    def test_dry_run_issues_no_writes_at_all(self, wp_env: Path, tmp_path: Path, capsys):
        respx.get(_POSTS_URL).mock(return_value=_posts_response([_row()]))
        update = respx.post(f"{_POSTS_URL}/101").mock(return_value=Response(200, json={}))

        rc = backfill.main(
            ["--dry-run", "--backup-dir", str(tmp_path / "backups")],
            execute_fn=_fake_execute("gps-tracker"),
        )

        assert rc == 0
        assert not update.called
        assert not (tmp_path / "backups").exists()
        assert not (wp_env / "state" / "product_usage.jsonl").exists()
        out = capsys.readouterr().out
        assert "gps-tracker: PawTrack GPS Collar" in out
        assert "<!-- blog-picks-block:v1 -->" in out
        assert "would-update=1" in out

    @respx.mock
    def test_elementor_post_is_never_patched(self, wp_env: Path, tmp_path: Path, capsys):
        row = _row(id=3308, slug="canned-pumpkin-dogs-guide", meta={"_elementor_data": '[{"a":1}]'})
        respx.get(_POSTS_URL).mock(return_value=_posts_response([row]))
        update = respx.post(f"{_POSTS_URL}/3308").mock(return_value=Response(200, json={}))

        rc = backfill.main(
            ["--backup-dir", str(tmp_path / "b")], execute_fn=_fake_execute("gps-tracker")
        )

        assert rc == 0
        assert not update.called
        assert "skip-elementor=1" in capsys.readouterr().out

    @respx.mock
    def test_recipe_post_skipped_by_default(self, wp_env: Path, tmp_path: Path, capsys):
        body = f"<p>Treats.</p>{recipe_block.BLOCK_MARKER_OPEN}old{recipe_block.BLOCK_MARKER_CLOSE}"
        respx.get(_POSTS_URL).mock(return_value=_posts_response([_row(content={"raw": body})]))
        update = respx.post(f"{_POSTS_URL}/101").mock(return_value=Response(200, json={}))

        rc = backfill.main(
            ["--backup-dir", str(tmp_path / "b")], execute_fn=_fake_execute("gps-tracker")
        )

        assert rc == 0
        assert not update.called
        assert "skip-has-recipe-block=1" in capsys.readouterr().out

    @respx.mock
    def test_include_recipe_blocks_refreshes_between_recipe_markers(
        self, wp_env: Path, tmp_path: Path
    ):
        body = (
            "<p>Treats.</p>\n"
            f"{recipe_block.BLOCK_MARKER_OPEN}\n<h2>Our Pick: Tools Used in This Recipe</h2>\n"
            f"<ul><li>old</li></ul>\n{recipe_block.BLOCK_MARKER_CLOSE}\n<h2>FAQ</h2>"
        )
        respx.get(_POSTS_URL).mock(return_value=_posts_response([_row(content={"raw": body})]))
        update = respx.post(f"{_POSTS_URL}/101").mock(return_value=Response(200, json={}))

        rc = backfill.main(
            ["--include-recipe-blocks", "--backup-dir", str(tmp_path / "b")],
            execute_fn=_fake_execute("run-harness"),
        )

        content = json.loads(update.calls.last.request.read())["content"]
        assert rc == 0
        assert content.count(recipe_block.BLOCK_MARKER_OPEN) == 1
        assert "<!-- blog-picks-block:v1 -->" not in content
        assert "<h2>Our Pick: Tools Used in This Recipe</h2>" in content
        assert "TrailBlazer Harness" in content
        assert "<li>old</li>" not in content

    @respx.mock
    def test_selector_failure_skips_without_posting(self, wp_env: Path, tmp_path: Path, capsys):
        respx.get(_POSTS_URL).mock(return_value=_posts_response([_row()]))
        update = respx.post(f"{_POSTS_URL}/101").mock(return_value=Response(200, json={}))

        rc = backfill.main(["--backup-dir", str(tmp_path / "b")], execute_fn=lambda *_a: None)

        assert rc == 0
        assert not update.called
        assert "skip-selector-failed=1" in capsys.readouterr().out

    @respx.mock
    def test_empty_selection_skips(self, wp_env: Path, tmp_path: Path, capsys):
        respx.get(_POSTS_URL).mock(return_value=_posts_response([_row()]))
        update = respx.post(f"{_POSTS_URL}/101").mock(return_value=Response(200, json={}))

        rc = backfill.main(["--backup-dir", str(tmp_path / "b")], execute_fn=_fake_execute())

        assert rc == 0
        assert not update.called
        assert "skip-no-fit=1" in capsys.readouterr().out

    @respx.mock
    def test_rerun_over_an_identical_block_is_unchanged(self, wp_env: Path, tmp_path: Path, capsys):
        post = WpPost(id=101, slug="dog-gps-guide", title="t", content=_BODY, meta={})
        _, already = render_for_mode(post, [_entry("gps-tracker")], "insert", "dogfoodandfun-20")
        respx.get(_POSTS_URL).mock(return_value=_posts_response([_row(content={"raw": already})]))
        update = respx.post(f"{_POSTS_URL}/101").mock(return_value=Response(200, json={}))

        rc = backfill.main(
            ["--backup-dir", str(tmp_path / "b")], execute_fn=_fake_execute("gps-tracker")
        )

        assert rc == 0
        assert not update.called
        assert "unchanged=1" in capsys.readouterr().out

    @respx.mock
    def test_failed_update_exits_nonzero(self, wp_env: Path, tmp_path: Path, capsys):
        respx.get(_POSTS_URL).mock(return_value=_posts_response([_row()]))
        respx.post(f"{_POSTS_URL}/101").mock(return_value=Response(403, text="forbidden"))

        rc = backfill.main(
            ["--backup-dir", str(tmp_path / "b")], execute_fn=_fake_execute("gps-tracker")
        )

        assert rc == 1
        assert "FAILED=1" in capsys.readouterr().out

    @respx.mock
    def test_pagination_and_only_slug_filter(self, wp_env: Path, tmp_path: Path):
        listing = respx.get(_POSTS_URL).mock(
            side_effect=[
                _posts_response([_row()], total_pages=2),
                _posts_response([_row(id=102, slug="dog-harness-guide")], total_pages=2),
            ]
        )
        respx.post(f"{_POSTS_URL}/101").mock(return_value=Response(200, json={}))

        rc = backfill.main(
            ["--only", "dog-gps-guide", "--limit", "1", "--backup-dir", str(tmp_path / "b")],
            execute_fn=_fake_execute("gps-tracker"),
        )

        assert rc == 0
        assert len(listing.calls) == 2
        assert listing.calls[0].request.url.params["slug"] == "dog-gps-guide"
        assert listing.calls[1].request.url.params["page"] == "2"
