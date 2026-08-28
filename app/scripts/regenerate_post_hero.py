"""Regenerate one published/draft post's hero image from the reference library.

Exists because a hero can be wrong for reasons that have nothing to do with
the post's text -- the library gained a photo, a mis-tagged reference got
corrected, or (the case this was written for) the anchor resolved to a photo
the mascot was not in and the model invented a different dog. Rewriting the
whole post to fix its picture is the wrong trade; this replaces the image and
touches nothing else.

Anchors with `prefer_mascot=True`, the same setting the drafting pipeline
uses, so the regenerated image is grounded on the real mascot wherever the
library can supply her.

KNOWN LIMITATION -- the FIFU plugin usually wins this fight. It serves the
hero from a slug-named file, and at post CREATION that file is the new image,
which is why the drafting pipeline sets a hero reliably. On an UPDATE the
slug-named file still holds the OLD image, and FIFU re-derives `featured_media`
from it within a second, discarding both the new attachment and the
`fifu_image_url` meta written alongside it. So this script verifies the
displayed image actually changed and reports failure when it did not, rather
than trusting an HTTP 200. When it fails, redrafting the post is the reliable
route: trash it, reset the idea row to `approved`, and re-run
`crewai_content_pipeline.py --idea-id ...`.

    python scripts/regenerate_post_hero.py --post-id 4690 --dry-run
    python scripts/regenerate_post_hero.py --post-id 4690 --category studio-mascot
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ENGINE_ROOT = Path(__file__).resolve().parent.parent
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

import httpx

from lib.crew.brand_identity import read_brand_identity
from lib.crew.reference_clauses import reference_clause
from lib.crew.reference_library import resolve_reference
from lib.crew.wp_image import build_image_brief, generate_wp_image
from lib.crew.wp_media import upload_wp_media
from lib.local_env import load_brand_env_into_environ, load_local_env
from lib.observability import get_logger
from lib.sessions.wp_client import wp_client

logger = get_logger(__name__)


def _infer_brand_dir() -> Path:
    brand_dir_env = os.environ.get("BRAND_DIR")
    if brand_dir_env:
        return Path(brand_dir_env)
    brands_root = _ENGINE_ROOT / "brands"
    candidates = sorted(
        d for d in brands_root.glob("*") if d.is_dir() and not d.name.startswith((".", "_"))
    )
    if len(candidates) == 1:
        return candidates[0]
    raise SystemExit("BRAND_DIR not set -- pass --brand-dir")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--post-id", type=int, required=True)
    parser.add_argument("--brand-dir", type=Path, default=None)
    parser.add_argument(
        "--category",
        default="",
        help="reference category to anchor on (default: let the library choose)",
    )
    parser.add_argument(
        "--angle",
        default="",
        help="mascot angle for the scene brief; defaults to the post title alone",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report the anchor and brief, generate and upload nothing",
    )
    return parser.parse_args()


def _displayed_image_bytes(post_id: int) -> bytes | None:
    """SHA-able bytes of whatever hero the post currently serves, or None.

    Read through the same `fifu_image_url` a visitor's browser would follow,
    because that -- not `featured_media` -- is what actually renders. Any
    failure returns None, which the caller treats as "cannot verify" rather
    than as success or failure.
    """
    try:
        with wp_client() as client:
            post = client.get(
                f"/wp-json/wp/v2/posts/{post_id}",
                params={"_fields": "meta", "context": "edit"},
            ).json()
        url = str((post.get("meta") or {}).get("fifu_image_url") or "")
        if not url:
            return None
        return httpx.get(url, timeout=60, follow_redirects=True).content
    except Exception:
        return None


def main() -> int:
    args = _parse_args()
    brand_dir = (args.brand_dir or _infer_brand_dir()).resolve()
    load_brand_env_into_environ(brand_dir)
    load_local_env()

    identity = read_brand_identity(brand_dir)

    before_bytes = _displayed_image_bytes(args.post_id)

    reference = resolve_reference(
        brand_dir, args.category or None, seed=str(args.post_id), prefer_mascot=True
    )
    if reference is None:
        print("ERROR: no reference photo resolved -- refusing to generate", file=sys.stderr)
        return 1

    print(f"anchor       : {reference.category}/{reference.path.name}")
    print(f"shows_mascot : {reference.shows_mascot}")
    if not reference.shows_mascot:
        # Refuse rather than repeat the failure this script exists to fix.
        print("ERROR: resolved anchor does not show the mascot", file=sys.stderr)
        return 1

    with wp_client() as client:
        post = client.get(
            f"/wp-json/wp/v2/posts/{args.post_id}",
            params={"_fields": "id,title,slug,status", "context": "edit"},
        ).json()
        title = (post.get("title") or {}).get("raw") or ""
        slug = post.get("slug") or f"post-{args.post_id}"
        print(f"post         : {args.post_id} [{post.get('status')}] {title[:60]}")

        brief = build_image_brief(title, args.angle)
        clause = reference_clause(
            reference, identity.mascot_name, identity.mascot_kind, identity.persona_name
        )
        print(f"brief        : {brief[:110]}")

        if args.dry_run:
            print("dry-run      : nothing generated or uploaded")
            return 0

        image = generate_wp_image(
            brief,
            alt_hint=title,
            mascot_name=identity.mascot_name,
            reference_image_bytes=reference.path.read_bytes(),
            reference_image_mime=reference.content_type,
            reference_clause=clause,
        )
        media_id, source_url = upload_wp_media(client, image, slug)
        print(f"uploaded     : media_id={media_id} {source_url}")

        resp = client.post(
            f"/wp-json/wp/v2/posts/{args.post_id}",
            json={
                "featured_media": media_id,
                # FIFU meta is required alongside featured_media or the theme
                # renders no image at all; see lib/crew/draft.py.
                "meta": {
                    "fifu_image_url": source_url,
                    "fifu_image_alt": image.alt_text or title,
                    "_elementor_edit_mode": "",
                },
            },
        )
        if resp.status_code >= 400:
            print(
                f"ERROR: post update failed {resp.status_code} {resp.text[:200]}", file=sys.stderr
            )
            return 1

    # Verify rather than trust the 200: FIFU can silently re-point the hero
    # back at the old slug-named file a second after a successful-looking POST.
    displayed = _displayed_image_bytes(args.post_id)
    if displayed is not None and displayed == before_bytes:
        print(
            "ERROR: post still displays the previous image -- FIFU re-pointed it.\n"
            f"       The new image is uploaded as media {media_id} ({source_url}).\n"
            "       Redraft instead: trash the post, set its idea row back to "
            "'approved', and re-run crewai_content_pipeline.py --idea-id <id>.",
            file=sys.stderr,
        )
        return 1

    print(f"updated      : post {args.post_id} hero replaced (verified)")
    logger.info(
        "post_hero_regenerated",
        post_id=args.post_id,
        anchor=reference.id,
        media_id=media_id,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
