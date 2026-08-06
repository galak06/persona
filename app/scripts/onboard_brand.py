#!/usr/bin/env python3
"""Thin CLI wrapper over `lib.brand_provisioning.provision_brand`.

Scriptable onboarding path outside the API (`app/api/brands_api.py`,
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

    # After filling in the new brand's real WP_URL/WP_USER/WP_APP_PASSWORD
    # in brands/<slug>/.env (provisioning only writes a credential-less
    # stub), confirm WordPress access actually works:
    python scripts/onboard_brand.py --verify-wp brands/acme-dogs
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
from lib.local_env import load_brand_env_into_environ, load_local_env
from lib.sessions.wp_client import wp_health_probe


def _csv_list(raw: str | None) -> list[str]:
    """Comma-separated CLI value -> list of trimmed, non-empty strings."""
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Onboard a new brand (folder + config + schedule)")
    parser.add_argument(
        "--verify-wp",
        type=Path,
        default=None,
        metavar="BRAND_DIR",
        help="Run the WordPress health probe against an already-onboarded brand's "
        "real .env (filled in after provisioning, since provision_brand() only "
        "writes a credential-less stub) and exit -- skips onboarding entirely.",
    )
    parser.add_argument("--name", required=False)
    parser.add_argument("--site-url", required=False)
    parser.add_argument("--niche", required=False)
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


def _run_verify_wp(brand_dir: Path) -> int:
    """Load `brand_dir`'s real .env and run `wp_health_probe()` -- the check
    an operator runs once they've filled in real WP_URL/WP_USER/WP_APP_PASSWORD
    after onboarding, to catch a broken WordPress connection (e.g. a
    hosting-level Application-Passwords lockout) before any content pipeline
    silently 401s against it."""
    load_brand_env_into_environ(brand_dir)
    load_local_env()
    result = wp_health_probe()
    print(json.dumps(dataclasses.asdict(result), indent=2))
    return 0 if result.ok else 1


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
    if args.verify_wp is not None:
        return _run_verify_wp(args.verify_wp)
    if not (args.name and args.site_url and args.niche):
        print(
            "ERROR: --name/--site-url/--niche are required unless --verify-wp is given",
            file=sys.stderr,
        )
        return 2
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
