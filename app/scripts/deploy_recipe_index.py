"""Deploy the dynamic /recipes/ index: `[dff_recipe_index]` snippet + page 3314.

Replaces the deleted baked-static `update_recipes_page.py`. The flow: upsert
the PHP shortcode source (`$BRAND_DIR/wordpress-snippets/dff-recipe-index.php`,
`<?php` header stripped) into the Code Snippets plugin via its REST API —
created INACTIVE and activated only after the plugin's server-side parse lint
(`code_error`) comes back null — then PATCH page 3314 with the shortcode-only
page template (`dff-recipe-index-page.html`), then verify the live page
renders `.dff-card` markup, auto-rolling back (page content + prior snippet
active-state) from the pre-write backup on zero cards or non-200.

HARD SCOPE GUARD: the only live objects this script ever writes are WP page
3314 (`_PAGE_ID`), the one snippet it created or matched by name/code-marker,
and a local backup file. No other page, post, snippet, or setting is touched.

Run::

    python -m scripts.deploy_recipe_index --dry-run
    python -m scripts.deploy_recipe_index
    python -m scripts.deploy_recipe_index --rollback <backup.json>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.errors.configuration import ConfigurationError
from lib.local_env import load_brand_env_into_environ, load_local_env
from lib.observability import configure_logging, get_logger
from lib.sessions.wp_client import wp_client

logger = get_logger(__name__)

_PAGE_ID = 3314
_CATEGORY_ID = 41
_SNIPPET_NAME = "DFF — Recipe Index Shortcode"
_SNIPPETS_API = "/wp-json/code-snippets/v1/snippets"
_CODE_MARKER = "dff_recipe_index_shortcode"
_SHORTCODE = "[dff_recipe_index]"
_LIVE_URL = "https://dogfoodandfun.com/recipes/"
#: Host WAF 403s default/scripted UAs; the live verify must look like a browser.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
#: Full attribute value only — must NOT count `class="dff-card-img"` etc.
_CARD_RE = re.compile(r'class="dff-card"[ >]')

# Keep Elementor from hijacking the stored page body (same six keys the old
# script cleared; `_elementor_page_settings` is deliberately left alone).
_ELEMENTOR_META = {
    "_elementor_edit_mode": "",
    "_elementor_template_type": "",
    "_elementor_version": "",
    "_elementor_data": "",
    "_elementor_css": "",
    "_elementor_page_assets": "",
}


def _brand_dir() -> Path:
    raw = os.environ.get("BRAND_DIR")
    if not raw:
        raise ConfigurationError("BRAND_DIR unset — brand env loaders did not run or .env is absent")
    return Path(raw)


def _read_php_source(path: Path) -> str:
    """Snippet code for the plugin: the repo PHP file minus its `<?php` line.

    The plugin stores code WITHOUT the opening tag (the repo file keeps it so
    linters can parse the source); refuses a file that does not start with it.
    """
    if not path.is_file():
        raise ConfigurationError(f"PHP snippet source missing: {path}")
    lines = path.read_text(encoding="utf-8").split("\n")
    if lines[0].strip() != "<?php":
        raise ConfigurationError(f"{path} must start with a '<?php' line (got: {lines[0]!r})")
    return "\n".join(lines[1:]).lstrip("\n")


def _read_page_template(path: Path) -> str:
    """The new page-3314 content. Must carry the shortcode and ZERO baked cards."""
    if not path.is_file():
        raise ConfigurationError(f"Page template missing: {path}")
    text = path.read_text(encoding="utf-8")
    if _SHORTCODE not in text:
        raise ConfigurationError(f"{path} does not contain {_SHORTCODE} — refusing to deploy")
    if _CARD_RE.search(text):
        raise ConfigurationError(
            f"{path} contains baked .dff-card markup — the page payload must be shortcode-only"
        )
    return text


def _write_backup(client: httpx.Client, snippet_before: dict[str, Any] | None) -> Path:
    """Snapshot page 3314 (context=edit) + the pre-existing snippet, BEFORE any write."""
    resp = client.get(f"/wp-json/wp/v2/pages/{_PAGE_ID}", params={"context": "edit"})
    resp.raise_for_status()
    backup_dir = _brand_dir() / "backups" / "recipes_page"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = backup_dir / f"{_PAGE_ID}-{stamp}.json"
    tmp = path.with_name(f"{path.name}.tmp")
    body = json.dumps({"page": resp.json(), "snippet_before": snippet_before}, indent=2)
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)
    before_id = None if snippet_before is None else snippet_before.get("id")
    logger.info("recipe_index_backup_written", path=str(path), snippet_before_id=before_id)
    return path


def _find_snippet(client: httpx.Client) -> dict[str, Any] | None:
    """Match OUR snippet by exact name, else by code marker (survives renames)."""
    resp = client.get(_SNIPPETS_API)
    resp.raise_for_status()
    snippets = resp.json()
    for snippet in snippets:
        if snippet.get("name") == _SNIPPET_NAME:
            return dict(snippet)
    for snippet in snippets:
        if _CODE_MARKER in (snippet.get("code") or ""):
            return dict(snippet)
    return None


def _upsert_snippet(client: httpx.Client, code: str, existing: dict[str, Any] | None) -> dict[str, Any]:
    """POST a new INACTIVE snippet or PATCH the matched one; return a fresh GET.

    The returned dict carries the plugin's readonly `code_error` field — the
    caller must gate activation and the page PATCH on it being null.
    """
    if existing is None:
        desc = ("Server-rendered recipe card grid for /recipes/ (page 3314). Managed by "
                "app/scripts/deploy_recipe_index.py — do not edit in wp-admin.")
        payload = {"name": _SNIPPET_NAME, "desc": desc, "code": code,
                   "scope": "front-end", "active": False}
        resp = client.post(_SNIPPETS_API, json=payload)
        resp.raise_for_status()
        snippet_id = int(resp.json()["id"])
        logger.info("recipe_index_snippet_created", snippet_id=snippet_id, active=False)
    else:
        snippet_id = int(existing["id"])
        patch = {"name": _SNIPPET_NAME, "code": code, "scope": "front-end"}
        resp = client.patch(f"{_SNIPPETS_API}/{snippet_id}", json=patch)
        resp.raise_for_status()
        logger.info("recipe_index_snippet_updated", snippet_id=snippet_id)
    confirm = client.get(f"{_SNIPPETS_API}/{snippet_id}")
    confirm.raise_for_status()
    return dict(confirm.json())


def _activate_snippet(client: httpx.Client, snippet_id: int) -> dict[str, Any]:
    """PATCH `active: true`, then GET to confirm the plugin really flipped it."""
    resp = client.patch(f"{_SNIPPETS_API}/{snippet_id}", json={"active": True})
    resp.raise_for_status()
    confirm = client.get(f"{_SNIPPETS_API}/{snippet_id}")
    confirm.raise_for_status()
    snippet = dict(confirm.json())
    logger.info("recipe_index_snippet_activated", snippet_id=snippet_id, active=snippet.get("active"))
    return snippet


def _patch_page(client: httpx.Client, content: str) -> None:
    payload = {"content": content, "status": "publish", "meta": dict(_ELEMENTOR_META)}
    resp = client.patch(f"/wp-json/wp/v2/pages/{_PAGE_ID}", json=payload)
    resp.raise_for_status()
    logger.info("recipe_index_page_patched", page_id=_PAGE_ID,
                content_bytes=len(content.encode("utf-8")), link=resp.json().get("link"))


def _expected_count(client: httpx.Client) -> int:
    """Published posts in the Recipes category, via the X-WP-Total header."""
    params: dict[str, str | int] = {"categories": _CATEGORY_ID, "per_page": 1, "_fields": "id"}
    resp = client.get("/wp-json/wp/v2/posts", params=params)
    resp.raise_for_status()
    return int(resp.headers.get("X-WP-Total", "0"))


def _verify_live() -> tuple[int, int]:
    """GET the live page (cache-busted, browser UA) -> (status, dff-card count)."""
    url = f"{_LIVE_URL}?dffcb={int(time.time())}"
    headers = {"User-Agent": _BROWSER_UA}
    resp = httpx.get(url, headers=headers, timeout=30.0, follow_redirects=True)
    return resp.status_code, len(_CARD_RE.findall(resp.text))


def _rollback(client: httpx.Client, backup_path: Path) -> None:
    """Restore page 3314 + snippet active-state from a `_write_backup` file."""
    backup = json.loads(backup_path.read_text(encoding="utf-8"))
    page = backup["page"]
    meta_before = page.get("meta") or {}
    payload = {
        "content": page["content"]["raw"],
        "status": page.get("status", "publish"),
        "meta": {key: meta_before.get(key, "") for key in _ELEMENTOR_META},
    }
    resp = client.patch(f"/wp-json/wp/v2/pages/{_PAGE_ID}", json=payload)
    resp.raise_for_status()
    logger.info("recipe_index_rollback_page_restored", page_id=_PAGE_ID, backup=str(backup_path))
    snippet_before = backup.get("snippet_before")
    if snippet_before is not None:
        active = bool(snippet_before.get("active"))
        resp = client.patch(f"{_SNIPPETS_API}/{snippet_before['id']}", json={"active": active})
        resp.raise_for_status()
        logger.info("recipe_index_rollback_snippet_state_restored",
                    snippet_id=snippet_before["id"], active=active)
        return
    current = _find_snippet(client)  # we created it this run — switch it off
    if current is not None:
        resp = client.patch(f"{_SNIPPETS_API}/{current['id']}", json={"active": False})
        resp.raise_for_status()
        logger.info("recipe_index_rollback_snippet_deactivated", snippet_id=current["id"])


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deploy the [dff_recipe_index] snippet + shortcode-only /recipes/ page (3314)."
    )
    parser.add_argument("--dry-run", action="store_true", help="print both payloads, write nothing")
    parser.add_argument("--rollback", metavar="FILE", help="restore from a backup JSON, then exit")
    parser.add_argument("--skip-verify", action="store_true", help="skip the live-page GET check")
    parser.add_argument("--log-level", default="INFO", help="DEBUG | INFO | WARNING | ERROR")
    return parser.parse_args(argv)


def _deploy(client: httpx.Client, args: argparse.Namespace, php_code: str, page_content: str) -> int:
    existing = _find_snippet(client)
    backup_path = _write_backup(client, existing)  # BEFORE any write
    snippet = _upsert_snippet(client, php_code, existing)
    snippet_id = int(snippet["id"])
    code_error = snippet.get("code_error")
    if code_error is not None:  # parse lint failed — page is never touched
        logger.error("recipe_index_snippet_code_error", snippet_id=snippet_id, code_error=code_error)
        client.patch(f"{_SNIPPETS_API}/{snippet_id}", json={"active": False}).raise_for_status()
        logger.info("recipe_index_snippet_deactivated", snippet_id=snippet_id)
        return 1
    if not _activate_snippet(client, snippet_id).get("active"):
        logger.error("recipe_index_snippet_activation_unconfirmed", snippet_id=snippet_id)
        return 1
    _patch_page(client, page_content)
    expected = _expected_count(client)
    if args.skip_verify:
        logger.info("recipe_index_verify_skipped", expected=expected, backup=str(backup_path))
        return 0
    status, live = _verify_live()
    if status != 200 or live == 0:
        logger.error("recipe_index_live_verify_failed", status=status, live_cards=live, expected=expected)
        _rollback(client, backup_path)
        return 1
    if live != expected:  # warn only — usually a stale page cache, not a failure
        logger.warning("recipe_index_count_mismatch", expected=expected, live_cards=live)
    else:
        logger.info("recipe_index_live_verified", expected=expected, live_cards=live)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    load_brand_env_into_environ()
    load_local_env()
    configure_logging(level=args.log_level)
    try:
        if args.rollback:
            with wp_client() as client:
                _rollback(client, Path(args.rollback))
            return 0
        assets = _brand_dir() / "wordpress-snippets"
        php_code = _read_php_source(assets / "dff-recipe-index.php")
        page_content = _read_page_template(assets / "dff-recipe-index-page.html")
        if args.dry_run:
            print(f"--- snippet code ({len(php_code):,} chars) ---\n{php_code}")
            print(f"--- page {_PAGE_ID} content ({len(page_content):,} chars) ---\n{page_content}")
            logger.info("recipe_index_dry_run", snippet_chars=len(php_code), page_chars=len(page_content))
            return 0
        with wp_client() as client:
            return _deploy(client, args, php_code, page_content)
    except (ConfigurationError, httpx.HTTPError) as exc:
        logger.error("recipe_index_deploy_failed", error=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
