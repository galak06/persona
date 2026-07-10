#!/usr/bin/env python3
"""Thin CLI wrapper over `lib.brand_provisioning.provision_brand`.

Scriptable onboarding path outside the API (`persona/api/brands_api.py`,
built in a parallel task) -- useful for local dry-runs and for the
dogfoodandfun re-onboarding steps in the plan's PR3 section.

Deliberately does NOT import `lib.bootstrap`/`lib.config`: onboarding a new
brand must work regardless of whatever `BRAND_DIR` (if any) happens to be
set in the current shell -- see `lib/brand_provisioning.py`'s module
docstring.

Usage:
    python scripts/onboard_brand.py \\
        --name "Acme Dogs" --site-url https://acmedogs.example \\
        --niche "dog nutrition" --target-audience "new dog owners" \\
        --mascot-name Rex --brand-persona "Rex's Human" \\
        --instagram-profile-url https://instagram.com/acmedogs \\
        --facebook-page-url https://facebook.com/acmedogs \\
        --primary-keywords "dog food,nutrition" \\
        --secondary-keywords "gps,running" \\
        --competitor-mentions "brand x,brand y" \\
        --competitor-accounts "@rival1,@rival2" \\
        --dry-run
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "lib"))

from lib.brand_provisioning import provision_brand
from lib.brand_templates import BrandSpec


def _csv_list(raw: str | None) -> list[str]:
    """Comma-separated CLI value -> list of trimmed, non-empty strings."""
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Onboard a new brand (folder + config + schedule)")
    parser.add_argument("--name", required=True)
    parser.add_argument("--site-url", required=True)
    parser.add_argument("--niche", required=True)
    parser.add_argument("--target-audience", default="")
    parser.add_argument("--mascot-name", default="")
    parser.add_argument("--brand-persona", default="")
    parser.add_argument("--instagram-profile-url", default="")
    parser.add_argument("--facebook-page-url", default="")
    parser.add_argument("--primary-keywords", default="", help="comma-separated")
    parser.add_argument("--secondary-keywords", default="", help="comma-separated")
    parser.add_argument("--competitor-mentions", default="", help="comma-separated")
    parser.add_argument("--competitor-accounts", default="", help="comma-separated")
    parser.add_argument(
        "--dry-run", action="store_true", help="Render + preview only, no disk/DB writes"
    )
    return parser.parse_args(argv)


def _build_spec(args: argparse.Namespace) -> BrandSpec:
    return BrandSpec(
        name=args.name,
        site_url=args.site_url,
        niche=args.niche,
        target_audience=args.target_audience,
        mascot_name=args.mascot_name,
        brand_persona=args.brand_persona,
        instagram_profile_url=args.instagram_profile_url,
        facebook_page_url=args.facebook_page_url,
        primary_keywords=_csv_list(args.primary_keywords),
        secondary_keywords=_csv_list(args.secondary_keywords),
        competitor_mentions=_csv_list(args.competitor_mentions),
        competitor_accounts=_csv_list(args.competitor_accounts),
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    spec = _build_spec(args)
    result = provision_brand(spec, dry_run=args.dry_run)

    payload = dataclasses.asdict(result)
    payload["brand_dir"] = str(result.brand_dir)
    payload["dry_run"] = args.dry_run
    print(json.dumps(payload, indent=2))

    for warning in result.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
