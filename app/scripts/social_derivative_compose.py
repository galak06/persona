"""Compose ONE product spotlight. Worker entry point.

    python -m scripts.social_derivative_compose --derivative-id <content_derivatives id>
    python -m scripts.social_derivative_compose --derivative-id <id> --dry-run

Dispatched by `POST /api/v1/social-derivatives` onto the shared `flow-run`
Redis queue and executed by `scripts/task_worker.py`, for the same reason
composition and the hook-image retry run there: the API image carries no LLM or
image-model credentials and no font stack, and a writer call plus a generation
plus an overlay pass needs all three.

**A separate script from `crewai_social_posts_pipeline.py` on purpose.** That
one publishes -- it is the module the hourly release flow runs -- so a compose
mode bolted onto it would be one mistaken argument away from posting to
Facebook under an affiliate link. This file imports no publisher, no worker and
no release sweep; `tests/test_social_derivative_compose_script.py` and
`tests/test_social_derivatives_create_api.py` both assert that on the AST, the
second of them against the script name the API actually dispatches.

Everything it does is in `lib.crew.spotlight.compose`; this is argument
parsing, an environment pre-flight, and a `summary:` line for
`worker_runs.message`.
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

from lib import derivatives_db
from lib.crew.spotlight.compose import compose_spotlight
from lib.local_env import load_brand_env_into_environ, load_local_env

# The writer LLM, the image model, WordPress (the post's body is what every
# claim is grounded in) -- and, unlike every other compose, the Associates tag:
# a spotlight whose whole purpose is an affiliate link cannot run without one,
# and finding that out after the writer and image calls have been paid for is
# the expensive way to learn it.
_REQUIRED_ENV_VARS = (
    "DEEPSEEK_API_KEY",
    "GEMINI_API_KEY",
    "WP_URL",
    "WP_USER",
    "WP_APP_PASSWORD",
    "AMAZON_ASSOCIATES_TAG",
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compose one product-spotlight social post.")
    p.add_argument("--derivative-id", type=str, required=True)
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="draft the captions and log the plan, then stop: no image is "
        "generated and the row is left untouched at 'composing'",
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
        if not args.dry_run:
            # The row is already 'composing' -- the API minted it that way and
            # the review page is polling it. Leaving it would hold the picker
            # hostage for the twenty minutes the stale sweep takes to notice a
            # run that never started, and then blame a timeout for a missing
            # environment variable. A dry run changes no row, so it marks none.
            derivatives_db.mark_failed(args.derivative_id, error="missing_env")
        return 1

    result = compose_spotlight(args.derivative_id, brand_dir=brand_dir, dry_run=args.dry_run)
    print(f"summary: {json.dumps(asdict(result))}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
