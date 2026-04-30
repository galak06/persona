"""Phase 1 discovery (§5 A–D) — all read-only.

Collects:
  A. WP REST plugin inventory (auth'd)
  B. Homepage WP page (to inspect content.raw anchors)
  C. FB page identity check (both candidate IDs from §4)
  D. IG bio snapshot

Writes everything to brand-fix/runs/<stamp>/discovery.json.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

# Load env from settings.local.json before any HTTP call.
sys.path.insert(0, str(Path(__file__).parent))
import _env  # noqa: E402

_env.load()

import httpx  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"


def _wp_client() -> httpx.Client:
    base = os.environ["WP_URL"].rstrip("/")
    return httpx.Client(
        base_url=base,
        auth=(os.environ["WP_USER"], os.environ["WP_APP_PASSWORD"]),
        timeout=30.0,
        headers={"User-Agent": "brand-fix/0.1 (+dogfoodandfun.com)"},
    )


def _get(client: httpx.Client, url: str, **kwargs) -> dict:
    try:
        r = client.get(url, **kwargs)
        return {
            "url": url,
            "status": r.status_code,
            "ok": r.is_success,
            "body": r.json() if "application/json" in r.headers.get("content-type", "") else r.text[:4000],
        }
    except Exception as exc:
        return {"url": url, "status": None, "ok": False, "error": repr(exc)}


def _fb_get(page_id: str, token: str) -> dict:
    url = f"https://graph.facebook.com/v21.0/{page_id}"
    params = {
        "fields": "id,name,username,link,fan_count,followers_count,about,category",
        "access_token": token,
    }
    full = f"{url}?{urllib.parse.urlencode(params)}"
    try:
        r = httpx.get(url, params=params, timeout=30.0)
        redacted = full.replace(token, "<TOKEN>")
        return {
            "url": redacted,
            "status": r.status_code,
            "ok": r.is_success,
            "body": r.json(),
        }
    except Exception as exc:
        return {"url": url, "status": None, "ok": False, "error": repr(exc)}


def main() -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = RUNS / stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, object] = {"stamp": stamp, "checks": {}}

    # A. WP plugin inventory (requires manage_plugins cap; may 401/403)
    with _wp_client() as wp:
        results["checks"]["wp_root"] = _get(wp, "/wp-json/wp/v2/")
        results["checks"]["wp_plugins"] = _get(wp, "/wp-json/wp/v2/plugins")
        results["checks"]["wp_settings"] = _get(wp, "/wp-json/wp/v2/settings")
        results["checks"]["wp_pages_home"] = _get(
            wp,
            "/wp-json/wp/v2/pages",
            params={"slug": "home", "context": "edit", "per_page": 5},
        )
        results["checks"]["wp_pages_front"] = _get(
            wp,
            "/wp-json/wp/v2/pages",
            params={"per_page": 20, "context": "edit"},
        )
        results["checks"]["wp_page_disclosure"] = _get(
            wp,
            "/wp-json/wp/v2/pages",
            params={"slug": "disclosure", "context": "edit"},
        )
        results["checks"]["wp_page_methodology"] = _get(
            wp,
            "/wp-json/wp/v2/pages",
            params={"slug": "methodology", "context": "edit"},
        )

    # C. FB page identity — both candidate IDs
    fb_token = os.environ.get("FB_PAGE_TOKEN", "")
    results["checks"]["fb_page_settings_id"] = _fb_get(os.environ["FB_PAGE_ID"], fb_token)
    results["checks"]["fb_page_brand_review_id"] = _fb_get("61586923685573", fb_token)

    # D. IG bio snapshot
    ig_id = os.environ["IG_ACCOUNT_ID"]
    try:
        r = httpx.get(
            f"https://graph.facebook.com/v21.0/{ig_id}",
            params={
                "fields": "id,username,biography,followers_count,name,profile_picture_url,website",
                "access_token": fb_token,
            },
            timeout=30.0,
        )
        results["checks"]["ig_account"] = {"status": r.status_code, "ok": r.is_success, "body": r.json()}
    except Exception as exc:
        results["checks"]["ig_account"] = {"ok": False, "error": repr(exc)}

    out = run_dir / "discovery.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str))
    print(f"WROTE {out}")

    # Compact console summary
    print("\n=== Phase 1 Discovery Summary ===")
    for key, val in results["checks"].items():
        status = val.get("status")
        ok = "OK " if val.get("ok") else "FAIL"
        print(f"  [{ok}] {status} {key}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
