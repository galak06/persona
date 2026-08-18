"""End-to-end tests for `scripts/deploy_recipe_index.main()`.

Modeled on tests/test_backfill_blog_product_blocks.py: `respx` intercepts
every REST call at https://wp.test, the two env loaders are stubbed so a
developer's real settings are never read into os.environ mid-suite, and the
two brand-dir assets (PHP snippet + page template) come from `tmp_path`.
Fakes + fixture data live in ``tests/_deploy_recipe_index_fakes.py``.

The assertions that matter most are the negative ones: a dry run issues
ZERO writes, and a snippet whose server-side parse lint (`code_error`)
fails must never let page 3314 be touched.
"""
# ruff: noqa: S101

from __future__ import annotations

import json
from pathlib import Path

import pytest
import respx
from httpx import Request, Response
from scripts import deploy_recipe_index as deploy

from lib.errors.configuration import ConfigurationError
from tests._deploy_recipe_index_fakes import (
    OLD_RAW,
    PAGE_TEMPLATE,
    PHP_SOURCE,
    SIX_EMPTY_ELEMENTOR_KEYS,
    SNIPPET_NAME,
    WP_BASE,
    FakeWp,
    holds,
    wire,
)


@pytest.fixture
def brand_dir(tmp_path: Path) -> Path:
    snippets = tmp_path / "brand" / "wordpress-snippets"
    snippets.mkdir(parents=True)
    (snippets / "dff-recipe-index.php").write_text(PHP_SOURCE, encoding="utf-8")
    (snippets / "dff-recipe-index-page.html").write_text(PAGE_TEMPLATE, encoding="utf-8")
    return tmp_path / "brand"


@pytest.fixture
def wp_env(monkeypatch: pytest.MonkeyPatch, brand_dir: Path) -> Path:
    monkeypatch.setenv("WP_URL", WP_BASE)
    monkeypatch.setenv("WP_USER", "editor")
    monkeypatch.setenv("WP_APP_PASSWORD", "app-password")
    monkeypatch.setenv("BRAND_DIR", str(brand_dir))
    monkeypatch.setattr(deploy, "load_local_env", lambda *_a, **_k: 0)
    monkeypatch.setattr(deploy, "load_brand_env_into_environ", lambda *_a, **_k: 0)
    return brand_dir


def _existing(sid: int, **over: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": sid,
        "name": SNIPPET_NAME,
        "code": "old",
        "scope": "front-end",
        "active": True,
        "priority": 10,
        "code_error": None,
    }
    row.update(over)
    return row


class TestDeployRun:
    def test_dry_run_issues_no_writes_at_all(self, wp_env: Path):
        with respx.mock(assert_all_called=False) as router:
            wp = FakeWp()
            wire(router, wp)
            rc = deploy.main(["--dry-run"])
            methods = {call.request.method for call in router.calls}
        assert rc == 0
        assert wp.snippet_posts == []
        assert wp.snippet_patches == []
        assert wp.page_writes == []
        assert methods <= {"GET"}, f"dry-run must be read-only, saw {methods}"
        assert not (wp_env / "backups").exists()

    def test_create_path_posts_inactive_then_activates_after_lint(self, wp_env: Path):
        with respx.mock(assert_all_called=False) as router:
            wp = FakeWp()  # no existing snippet -> create path
            wire(router, wp)
            rc = deploy.main(["--skip-verify"])

        assert rc == 0
        assert deploy._SNIPPET_NAME == SNIPPET_NAME
        [posted] = wp.snippet_posts
        assert posted["name"] == SNIPPET_NAME
        assert posted["scope"] == "front-end"
        assert posted["active"] is False, "snippet must be created INACTIVE"
        assert "dff_recipe_index_shortcode" in posted["code"]
        assert "<?php" not in posted["code"]

        events = wp.events
        i_post = events.index("snippet-POST")
        i_activate = events.index("snippet-PATCH-activate")
        lint_reads = [
            i
            for i, event in enumerate(events)
            if event.startswith("snippet-GET") or event == "snippets-list"
        ]
        assert any(i_post < i < i_activate for i in lint_reads), (
            f"activation must follow a code_error GET: {events}"
        )
        assert i_activate < events.index("page-WRITE"), events

    def test_code_error_blocks_page_and_deactivates_snippet(self, wp_env: Path):
        with respx.mock(assert_all_called=False) as router:
            wp = FakeWp(snippets=[_existing(12)], code_error=["unexpected end of file", 2])
            wire(router, wp)
            rc = deploy.main(["--skip-verify"])

        assert rc != 0
        assert wp.page_writes == [], "page must never be written when the lint fails"
        assert any(patch.get("active") is False for _sid, patch in wp.snippet_patches), (
            wp.snippet_patches
        )

    def test_existing_snippet_by_name_is_patched_not_posted(self, wp_env: Path):
        with respx.mock(assert_all_called=False) as router:
            wp = FakeWp(snippets=[_existing(12)])
            wire(router, wp)
            rc = deploy.main(["--skip-verify"])
        assert rc == 0
        assert wp.snippet_posts == [], "an existing snippet must be PATCHed, never re-POSTed"
        assert any(sid == 12 and "code" in patch for sid, patch in wp.snippet_patches)

    def test_existing_snippet_by_code_marker_survives_a_rename(self, wp_env: Path):
        renamed = _existing(
            13,
            name="Renamed in wp-admin",
            code="if (1) { function dff_recipe_index_shortcode() {} }",
        )
        with respx.mock(assert_all_called=False) as router:
            wp = FakeWp(snippets=[renamed])
            wire(router, wp)
            rc = deploy.main(["--skip-verify"])
        assert rc == 0
        assert wp.snippet_posts == []
        assert any(sid == 13 and "code" in patch for sid, patch in wp.snippet_patches)

    def test_page_payload_is_shortcode_only_with_cleared_meta(self, wp_env: Path):
        with respx.mock(assert_all_called=False) as router:
            wp = FakeWp()
            wire(router, wp)
            rc = deploy.main(["--skip-verify"])
        assert rc == 0
        payload = wp.page_writes[-1]
        assert "<!-- wp:shortcode -->" in payload["content"]
        assert "[dff_recipe_index]" in payload["content"]
        assert payload["content"].count('class="dff-card"') == 0, "no baked cards allowed"
        assert payload["meta"] == SIX_EMPTY_ELEMENTOR_KEYS
        assert payload["status"] == "publish"

    def test_backup_written_before_the_page_write(self, wp_env: Path):
        with respx.mock(assert_all_called=False) as router:
            wp = FakeWp()

            def _page_write_checks_backup(request: Request) -> Response:
                files = list((wp_env / "backups" / "recipes_page").glob("3314-*.json"))
                assert files, "backup must be written BEFORE the page is written"
                backup = json.loads(files[0].read_text(encoding="utf-8"))
                assert holds(backup, OLD_RAW), "backup must hold the prior content.raw"
                return wp.write_page(request)

            wire(router, wp, page_write=_page_write_checks_backup)
            rc = deploy.main(["--skip-verify"])
        assert rc == 0
        assert wp.page_writes, "the page write never happened"


class TestRollback:
    def test_rollback_restores_page_content_and_snippet_state(self, wp_env: Path):
        with respx.mock(assert_all_called=False) as router:
            wp = FakeWp(snippets=[_existing(12, active=False)])
            wire(router, wp)
            assert deploy.main(["--skip-verify"]) == 0, "seed deploy must succeed"

        backups = sorted((wp_env / "backups" / "recipes_page").glob("3314-*.json"))
        assert backups, "deploy must leave a backup file behind"

        with respx.mock(assert_all_called=False) as router:
            wp_after = FakeWp(snippets=[dict(wp.snippets[12])])  # now active, new code
            wp_after.page["content"] = dict(wp.page["content"])  # deployed template
            wire(router, wp_after)
            rc = deploy.main(["--rollback", str(backups[-1])])

        assert rc == 0
        assert wp_after.page_writes, "rollback must write the page"
        assert wp_after.page_writes[-1]["content"] == OLD_RAW
        assert any(
            sid == 12 and patch.get("active") is False for sid, patch in wp_after.snippet_patches
        ), f"prior active-state (False) must be restored: {wp_after.snippet_patches}"


class TestReadPhpSource:
    def test_strips_the_php_header_line(self, tmp_path: Path):
        source = tmp_path / "snippet.php"
        source.write_text(PHP_SOURCE, encoding="utf-8")
        code = deploy._read_php_source(source)
        assert "<?php" not in code
        assert "dff_recipe_index_shortcode" in code
        assert "add_shortcode" in code

    def test_rejects_a_file_without_the_php_header(self, tmp_path: Path):
        bad = tmp_path / "bad.php"
        bad.write_text("function nope() {}\n", encoding="utf-8")
        with pytest.raises(ConfigurationError):
            deploy._read_php_source(bad)
