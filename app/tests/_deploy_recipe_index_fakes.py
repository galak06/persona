"""Fakes + fixtures data for ``tests/test_deploy_recipe_index.py``.

`FakeWp` is a stateful stand-in for the two REST surfaces the deployer
touches — the Code Snippets plugin API and core `wp/v2/pages` — recording
every write it receives so the tests can make ordering and negative
assertions. `wire()` registers it on a respx router (same seam the model
suite, tests/test_backfill_blog_product_blocks.py, fakes at).
"""

from __future__ import annotations

import json
from typing import Any

import respx
from httpx import Request, Response

WP_BASE = "https://wp.test"
SNIPPETS_URL = f"{WP_BASE}/wp-json/code-snippets/v1/snippets"
SNIPPET_ITEM_RE = r"https://wp\.test/wp-json/code-snippets/v1/snippets/(?P<sid>\d+)"
PAGE_URL = f"{WP_BASE}/wp-json/wp/v2/pages/3314"
POSTS_URL = f"{WP_BASE}/wp-json/wp/v2/posts"
LIVE_RE = r"https://dogfoodandfun\.com/recipes/.*"

SNIPPET_NAME = "DFF — Recipe Index Shortcode"

SIX_EMPTY_ELEMENTOR_KEYS = {
    "_elementor_edit_mode": "",
    "_elementor_template_type": "",
    "_elementor_version": "",
    "_elementor_data": "",
    "_elementor_css": "",
    "_elementor_page_assets": "",
}

#: What page 3314 holds BEFORE the deploy — baked static cards.
OLD_RAW = (
    '<!-- wp:html --><div class="dff-card">Baked Card One</div>'
    '<div class="dff-card">Baked Card Two</div><!-- /wp:html -->'
)

PHP_SOURCE = (
    "<?php\n/** Test copy of the recipe-index snippet. */\n"
    "if ( ! function_exists( 'dff_recipe_index_shortcode' ) ) {\n"
    "\tfunction dff_recipe_index_shortcode(): string {\n"
    "\t\treturn '<div id=\"dff-recipe-index\"></div>';\n\t}\n}\n"
    "add_shortcode( 'dff_recipe_index', 'dff_recipe_index_shortcode' );\n"
)

#: `.dff-card` appears only as a CSS selector — never as `class="dff-card"`.
PAGE_TEMPLATE = (
    "<!-- wp:html -->\n"
    '<link href="https://fonts.googleapis.com/css2?family=Fraunces" rel="stylesheet">\n'
    "<!-- /wp:html -->\n\n"
    "<!-- wp:shortcode -->\n[dff_recipe_index]\n<!-- /wp:shortcode -->\n\n"
    "<!-- wp:html -->\n"
    "<style>.dff-card{border-radius:14px}.dff-card[hidden]{display:none!important}</style>\n"
    "<script>document.getElementById('dff-search');</script>\n"
    "<!-- /wp:html -->\n"
)


def holds(obj: Any, needle: str) -> bool:
    """True when any string value anywhere inside `obj` contains `needle`."""
    if isinstance(obj, str):
        return needle in obj
    if isinstance(obj, dict):
        return any(holds(value, needle) for value in obj.values())
    if isinstance(obj, list):
        return any(holds(value, needle) for value in obj)
    return False


class FakeWp:
    """Stateful fake of the two REST surfaces the deployer touches."""

    def __init__(
        self, snippets: list[dict[str, Any]] | None = None, code_error: Any = None
    ) -> None:
        self.snippets = {row["id"]: dict(row) for row in (snippets or [])}
        self.code_error = code_error  # applied to any created/updated snippet
        self.events: list[str] = []
        self.snippet_posts: list[dict[str, Any]] = []
        self.snippet_patches: list[tuple[int, dict[str, Any]]] = []
        self.page_writes: list[dict[str, Any]] = []
        self.page: dict[str, Any] = {
            "id": 3314,
            "status": "publish",
            "content": {"raw": OLD_RAW, "rendered": OLD_RAW},
            "meta": {"_elementor_edit_mode": "builder", "_elementor_data": "[]"},
            "title": {"raw": "Recipes", "rendered": "Recipes"},
        }

    def list_snippets(self, _request: Request) -> Response:
        self.events.append("snippets-list")
        return Response(200, json=list(self.snippets.values()))

    def create_snippet(self, request: Request) -> Response:
        payload = json.loads(request.read())
        row = {"id": 77, "priority": 10, **payload, "code_error": self.code_error}
        row["active"] = bool(payload.get("active", False))
        self.snippets[77] = row
        self.snippet_posts.append(payload)
        self.events.append("snippet-POST")
        return Response(201, json=row)

    def get_snippet(self, _request: Request, sid: str) -> Response:
        self.events.append(f"snippet-GET-{sid}")
        row = self.snippets.get(int(sid))
        return Response(200, json=row) if row else Response(404, json={})

    def patch_snippet(self, request: Request, sid: str) -> Response:
        payload = json.loads(request.read())
        row = self.snippets.setdefault(int(sid), {"id": int(sid)})
        row.update(payload)
        if "code" in payload:
            row["code_error"] = self.code_error  # server-side parse lint
        self.snippet_patches.append((int(sid), payload))
        flavor = {True: "activate", False: "deactivate"}.get(payload.get("active"), "code")
        self.events.append(f"snippet-PATCH-{flavor}")
        return Response(200, json=row)

    def get_page(self, _request: Request) -> Response:
        self.events.append("page-GET")
        return Response(200, json=self.page)

    def write_page(self, request: Request) -> Response:
        payload = json.loads(request.read())
        self.page_writes.append(payload)
        self.events.append("page-WRITE")
        if "content" in payload:
            raw = payload["content"]
            self.page["content"] = {"raw": raw, "rendered": raw}
        return Response(200, json=self.page)


def wire(router: respx.MockRouter, wp: FakeWp, page_write: Any = None) -> None:
    """Register every route the deployer may hit; writes funnel into `wp`."""
    write = page_write or wp.write_page
    router.get(SNIPPETS_URL).mock(side_effect=wp.list_snippets)
    router.post(SNIPPETS_URL).mock(side_effect=wp.create_snippet)
    router.get(url__regex=SNIPPET_ITEM_RE).mock(side_effect=wp.get_snippet)
    router.patch(url__regex=SNIPPET_ITEM_RE).mock(side_effect=wp.patch_snippet)
    router.get(PAGE_URL).mock(side_effect=wp.get_page)
    for method in ("patch", "post", "put"):
        getattr(router, method)(PAGE_URL).mock(side_effect=write)
    router.get(POSTS_URL).mock(
        return_value=Response(200, json=[{"id": 1}], headers={"X-WP-Total": "2"})
    )
    router.get(url__regex=LIVE_RE).mock(
        return_value=Response(
            200, text='<div class="dff-card">a</div><div class="dff-card">b</div>'
        )
    )
