"""Phase 2 dry-run / apply — additive + minimal surgical edits.

Operations (ordered by blast radius):
  2.1  PATCH /wp/v2/pages/3228  (affiliate-disclosure)  — in-place rewrite
  2.2  POST  /wp/v2/pages                               — create /methodology/
  2.4  REST route for footer disclaimer via code-snippets plugin (prefers
       /wp-json/code-snippets/v2/snippets; falls back to a manual install note).

Dry-run (default): prints unified diff for each op, saves to runs/<stamp>/.
--apply: executes writes. Still prints diffs. Writes response bodies to runs/.
"""
from __future__ import annotations

import argparse
import difflib
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
PATCHES = ROOT / "patches"
RUNS = ROOT / "runs"


def _wp() -> httpx.Client:
    return httpx.Client(
        base_url=os.environ["WP_URL"].rstrip("/"),
        auth=(os.environ["WP_USER"], os.environ["WP_APP_PASSWORD"]),
        timeout=60.0,
        headers={"User-Agent": "brand-fix/0.1 (+dogfoodandfun.com)"},
    )


def _diff(a: str, b: str, a_label: str, b_label: str) -> str:
    return "".join(
        difflib.unified_diff(
            a.splitlines(keepends=True),
            b.splitlines(keepends=True),
            fromfile=a_label,
            tofile=b_label,
            n=2,
        )
    )


def op_21_affiliate_disclosure(wp: httpx.Client, run_dir: Path, apply: bool) -> dict:
    """2.1 — rewrite /affiliate-disclosure/ (id 3228) content.raw."""
    new_html = (PATCHES / "affiliate_disclosure_3228.html").read_text()
    r = wp.get("/wp-json/wp/v2/pages/3228", params={"context": "edit"})
    r.raise_for_status()
    current = r.json()
    cur_raw = current["content"]["raw"]
    if cur_raw.strip() == new_html.strip():
        return {"op": "2.1", "status": "NOOP", "note": "content.raw already matches patch"}

    diff = _diff(cur_raw, new_html, "live:3228", "patch:affiliate_disclosure_3228.html")
    (run_dir / "2.1_affiliate_disclosure.diff").write_text(diff)

    if not apply:
        return {"op": "2.1", "status": "DRY_RUN", "diff_bytes": len(diff), "target": "pages/3228"}

    r2 = wp.post("/wp-json/wp/v2/pages/3228", json={"content": new_html})
    r2.raise_for_status()
    (run_dir / "2.1_response.json").write_text(json.dumps(r2.json(), indent=2, ensure_ascii=False, default=str))
    return {"op": "2.1", "status": "APPLIED", "page_id": 3228, "link": r2.json().get("link")}


def op_22_methodology(wp: httpx.Client, run_dir: Path, apply: bool) -> dict:
    """2.2 — create /methodology/ page."""
    # First check it doesn't already exist (idempotency).
    r = wp.get("/wp-json/wp/v2/pages", params={"slug": "methodology", "context": "edit"})
    r.raise_for_status()
    if r.json():
        existing = r.json()[0]
        return {"op": "2.2", "status": "EXISTS", "page_id": existing["id"], "link": existing.get("link")}

    new_html = (PATCHES / "methodology_new.html").read_text()
    (run_dir / "2.2_methodology.html").write_text(new_html)

    payload = {
        "title": "Methodology",
        "slug": "methodology",
        "status": "publish",
        "content": new_html,
        "excerpt": "How I actually evaluate every product on Dog Food & Fun — the criteria, the math, and what I won't do.",
    }
    (run_dir / "2.2_payload.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    if not apply:
        return {"op": "2.2", "status": "DRY_RUN", "payload_bytes": len(new_html)}

    r2 = wp.post("/wp-json/wp/v2/pages", json=payload)
    r2.raise_for_status()
    body = r2.json()
    (run_dir / "2.2_response.json").write_text(json.dumps(body, indent=2, ensure_ascii=False, default=str))
    return {"op": "2.2", "status": "APPLIED", "page_id": body["id"], "link": body.get("link")}


def op_24_footer_snippet(wp: httpx.Client, run_dir: Path, apply: bool) -> dict:
    """2.4 — install footer disclaimer snippet via code-snippets REST API.

    The Code Snippets plugin exposes /wp-json/code-snippets/v2/snippets for
    authenticated admins. If the JWT-auth plugin paywalls this endpoint
    (memory: wp-rest-api-authentication free tier blocks 3rd-party REST),
    we fall back to a manual install note rather than a partial apply.
    """
    probe = wp.get("/wp-json/code-snippets/v2/snippets", params={"per_page": 1})
    reachable = probe.status_code == 200

    php_body = (PATCHES / "footer_snippet.php").read_text()
    # Strip opening <?php for the snippet body — Code Snippets plugin expects
    # raw PHP without the opening tag.
    snippet_code = php_body
    if snippet_code.startswith("<?php"):
        snippet_code = snippet_code.split("?>", 1)[0].lstrip()
        # Remove the leading `<?php` line, keep body.
        lines = snippet_code.splitlines()
        if lines and lines[0].strip().startswith("<?php"):
            snippet_code = "\n".join(lines[1:])

    payload = {
        "name": "brand-fix: sitewide footer disclaimer",
        "desc": "Adds footer disclaimer + methodology/affiliate-disclosure/disclaimer links. Installed by brand-fix.",
        "code": snippet_code,
        "scope": "global",
        "active": True,
        "tags": ["brand-fix"],
    }
    (run_dir / "2.4_payload.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str))

    if not reachable:
        return {
            "op": "2.4",
            "status": "FALLBACK",
            "reason": f"code-snippets REST returned {probe.status_code}",
            "note": "Install brand-fix/patches/footer_snippet.php manually via WP Admin → Snippets → Add New. "
                    "Paste the body (without the opening <?php), set scope=Run everywhere, save & activate.",
        }

    if not apply:
        return {"op": "2.4", "status": "DRY_RUN", "route": "code-snippets/v2/snippets", "snippet_bytes": len(snippet_code)}

    # Idempotency: skip if a snippet with this name already exists.
    existing = probe.json() if probe.status_code == 200 else []
    match = next((s for s in existing if s.get("name") == payload["name"]), None)
    if match:
        return {"op": "2.4", "status": "EXISTS", "snippet_id": match.get("id")}

    r2 = wp.post("/wp-json/code-snippets/v2/snippets", json=payload)
    r2.raise_for_status()
    body = r2.json()
    (run_dir / "2.4_response.json").write_text(json.dumps(body, indent=2, ensure_ascii=False, default=str))
    return {"op": "2.4", "status": "APPLIED", "snippet_id": body.get("id")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Actually commit writes (default: dry-run)")
    ap.add_argument("--stamp", default=datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))
    ap.add_argument("--ops", default="2.1,2.2,2.4", help="Comma-separated op ids to run")
    args = ap.parse_args()

    run_dir = RUNS / args.stamp
    run_dir.mkdir(parents=True, exist_ok=True)

    ops = {o.strip() for o in args.ops.split(",") if o.strip()}
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== brand-fix {mode} @ {args.stamp} → {run_dir} ===\n")

    results = []
    with _wp() as wp:
        if "2.1" in ops:
            results.append(op_21_affiliate_disclosure(wp, run_dir, args.apply))
        if "2.2" in ops:
            results.append(op_22_methodology(wp, run_dir, args.apply))
        if "2.4" in ops:
            results.append(op_24_footer_snippet(wp, run_dir, args.apply))

    summary = {"stamp": args.stamp, "mode": mode, "results": results}
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str))

    for r in results:
        op = r.get("op")
        status = r.get("status")
        extra = {k: v for k, v in r.items() if k not in {"op", "status"}}
        print(f"  [{status:<9}] {op}  {extra}")
    print(f"\nArtifacts: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
