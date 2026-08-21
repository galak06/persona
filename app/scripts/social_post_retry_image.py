"""Regenerate ONE queued social post's hook image. Worker entry point.

    python -m scripts.social_post_retry_image --idea-id <content_ideas id>
    python -m scripts.social_post_retry_image --idea-id <id> --reference-category studio-mascot

Dispatched by `POST /api/v1/social-posts/{id}/retry-image` onto the shared
`flow-run` Redis queue and executed by `scripts/task_worker.py`, for the same
reason composition runs there: the API image carries no LLM or image-model
credentials and no font stack, and a generation plus an overlay pass needs all
three.

**A separate script from `crewai_social_posts_pipeline.py` on purpose.** That
one publishes -- it is the module the hourly release flow runs -- so a retry
mode bolted onto it would be one mistaken argument away from posting to
Facebook, and the only thing standing between a click and a live post would be
an argument filter in the API. This file imports no publisher, no release
sweep and no platform client at all. There is no argument you can pass it that
publishes anything, because there is no code here that could.

Everything it does is in `lib.crew.socialpost.retry`; this is argument parsing,
an environment pre-flight, and a `summary:` line for `worker_runs.message`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

_ENGINE_ROOT = Path(__file__).resolve().parent.parent
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))

from lib.crew.socialpost.retry import retry_hook_image
from lib.local_env import load_brand_env_into_environ, load_local_env

# The writer LLM, the image model, and WordPress -- the post's own body is what
# the fresh image brief is written from. Same set composition needs, minus
# nothing: a retry redraws the image direction, so it is a full compose of the
# image half.
_REQUIRED_ENV_VARS = (
    "DEEPSEEK_API_KEY",
    "GEMINI_API_KEY",
    "WP_URL",
    "WP_USER",
    "WP_APP_PASSWORD",
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Regenerate one queued social post's hook image.")
    p.add_argument("--idea-id", type=str, required=True)
    p.add_argument(
        "--reference-category",
        type=str,
        default="",
        help="reference-library category slug to anchor the new image on; "
        "empty lets the fresh plan choose, falling back to any mascot photo",
    )
    p.add_argument("--brand-dir", type=Path, default=None)
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    brand_dir_env = os.environ.get("BRAND_DIR", "")
    if args.brand_dir is None and not brand_dir_env:
        print("ERROR: BRAND_DIR is not set and --brand-dir was not passed", file=sys.stderr)
        return 1
    brand_dir = (args.brand_dir or Path(brand_dir_env)).resolve()

    load_brand_env_into_environ(brand_dir)
    load_local_env()
    missing = [name for name in _REQUIRED_ENV_VARS if not os.environ.get(name, "").strip()]
    if missing:
        print(f"ERROR: missing required env var(s): {', '.join(missing)}", file=sys.stderr)
        return 1

    result = retry_hook_image(
        args.idea_id,
        brand_dir=brand_dir,
        reference_category=args.reference_category.strip(),
    )
    print(f"summary: {json.dumps(asdict(result))}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
