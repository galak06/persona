"""Pre-mutation backup (§13.5).

Writes timestamped JSON snapshots to brand-fix/backups/YYYYMMDD/:
  - wp-pages.json   — all pages with context=edit (raw content)
  - wp-posts.json   — all posts with context=edit
  - meta.json       — current FB `about` + IG `biography`

Idempotent per day — re-running overwrites YYYYMMDD contents.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import _env  # noqa: E402
_env.load()

import httpx  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BACKUPS = ROOT / "backups"


def _wp() -> httpx.Client:
    return httpx.Client(
        base_url=os.environ["WP_URL"].rstrip("/"),
        auth=(os.environ["WP_USER"], os.environ["WP_APP_PASSWORD"]),
        timeout=60.0,
        headers={"User-Agent": "brand-fix/0.1 (+dogfoodandfun.com)"},
    )


def _paginate(client: httpx.Client, path: str, **params) -> list[dict]:
    items: list[dict] = []
    page = 1
    while True:
        r = client.get(path, params={**params, "page": page, "per_page": 100, "context": "edit"})
        if r.status_code == 400 and "rest_post_invalid_page_number" in r.text:
            break
        r.raise_for_status()
        chunk = r.json()
        if not isinstance(chunk, list) or not chunk:
            break
        items.extend(chunk)
        total_pages = int(r.headers.get("X-WP-TotalPages", "1"))
        if page >= total_pages:
            break
        page += 1
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=datetime.now(timezone.utc).strftime("%Y%m%d"))
    args = ap.parse_args()

    out_dir = BACKUPS / args.day
    out_dir.mkdir(parents=True, exist_ok=True)

    with _wp() as wp:
        pages = _paginate(wp, "/wp-json/wp/v2/pages", status="any")
        posts = _paginate(wp, "/wp-json/wp/v2/posts", status="any")

    (out_dir / "wp-pages.json").write_text(json.dumps(pages, indent=2, ensure_ascii=False, default=str))
    (out_dir / "wp-posts.json").write_text(json.dumps(posts, indent=2, ensure_ascii=False, default=str))

    # FB/IG — best-effort; token may be missing/expired
    token = os.environ.get("FB_PAGE_TOKEN", "")
    meta: dict[str, object] = {}
    if token:
        try:
            r = httpx.get(
                f"https://graph.facebook.com/v23.0/{os.environ['FB_PAGE_ID']}",
                params={"fields": "id,name,about,link,fan_count,followers_count", "access_token": token},
                timeout=30.0,
            )
            meta["fb_page"] = {"status": r.status_code, "body": r.json()}
        except Exception as exc:
            meta["fb_page"] = {"error": repr(exc)}
        try:
            r = httpx.get(
                f"https://graph.facebook.com/v23.0/{os.environ['IG_ACCOUNT_ID']}",
                params={"fields": "id,username,name,biography,website,followers_count", "access_token": token},
                timeout=30.0,
            )
            meta["ig_account"] = {"status": r.status_code, "body": r.json()}
        except Exception as exc:
            meta["ig_account"] = {"error": repr(exc)}

    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False, default=str))

    print(f"  wp-pages.json  {len(pages):>4} pages")
    print(f"  wp-posts.json  {len(posts):>4} posts")
    print(f"  meta.json      FB={'ok' if meta.get('fb_page', {}).get('status') == 200 else 'n/a'} "
          f"IG={'ok' if meta.get('ig_account', {}).get('status') == 200 else 'n/a'}")
    print(f"WROTE {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
