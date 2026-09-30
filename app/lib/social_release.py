"""The release sweep: publish every social row whose scheduled time has come.

Approval SCHEDULES rather than publishes -- an approved row takes the next free
daily slot (``lib.social_post_slots``, allocated across both tables by
``lib.social_slot_allocator``) -- so this sweep is what actually posts. It is
the whole body of the hourly ``social-posts-release`` cron flow, and it also
runs at the tail of every non-``--compose-only`` invocation of
``scripts/crewai_social_posts_pipeline.py``, so any run flushes whatever became
due since the last one.

**Why it lives here and not in the pipeline script.** It was born inside
``crewai_social_posts_pipeline.py``, whose other two phases (detect-publish,
composition) have nothing to do with publishing. Adding the derivative track
(``content_derivatives`` -- product spotlights, a sibling table with its own
worker) would have pushed that script well past the repo's 300-line ceiling,
and more importantly the sweep had NO tests at all: it is the one code path
that spends money and posts publicly, and it was only reachable by running the
pipeline for real. A module of its own is directly callable, and therefore
directly testable with a faked ``subprocess.run`` (``tests/test_social_release.py``).

**Why every FB half runs before any IG half.** A row that publishes to Facebook
in this pass arms its Instagram due-time as a side effect (``set_fb_result``
stamps ``ig_due_at = NOW() + gap``), and that time is always in the future, so
the same row cannot also fire its IG half in the same pass. Interleaving the
passes -- or sweeping per row instead of per platform -- would reopen that door.
The two tables are swept in the same shape for the same reason: FB regular, FB
derivative, then IG regular, IG derivative.

**Why ONE shared ``error`` key.** The caller's exit code
(``crewai_social_posts_pipeline._exit_code``) treats *every* non-``error`` key
as a success and only fails a run that accomplished nothing. Giving each track
its own error key (``derivative_error``...) would silently make a run of pure
failures look successful. Successes are counted per track because the operator
wants to see which track published; failures are counted together because the
exit code needs exactly one name for "nothing worked".

Publishing is deliberately owned by one flow at a time: neither worker's status
guard is a fully atomic claim on the regular track, so two overlapping sweeps on
the same due row could both post it. That is why ``social-posts-compose`` never
releases and ``social-posts-release`` never composes.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from lib import derivatives_db, social_post_db
from lib.observability import get_logger

logger = get_logger(__name__)

# The publish workers are invoked as modules (``-m``), not as file paths: they
# import ``publishers.*`` / ``workers.*`` relative to ``recipe-publisher/``,
# which is only importable when that directory is the working directory.
REGULAR_WORKER = "workers.worker_wp_ideas_social_post"
DERIVATIVE_WORKER = "workers.worker_social_derivative"

_APP_ROOT = Path(__file__).resolve().parent.parent
_RECIPE_PUBLISHER_ROOT = _APP_ROOT / "recipe-publisher"
# One publish (WP media upload + a Meta container round-trip) has never come
# close to this; it exists so a hung API call cannot hold the hourly flow open.
_WORKER_TIMEOUT_SECONDS = 600

_IDEA_ID_FLAG = "--idea-id"
_DERIVATIVE_ID_FLAG = "--derivative-id"
_ERROR_KEY = "error"
_TRACK_SOCIAL_POST = "social_post"
_TRACK_DERIVATIVE = "derivative"


def publish_one(module: str, id_flag: str, row_id: str, platform: str, *, brand_dir: Path) -> bool:
    """Subprocess-invoke a publish worker for one row on one platform.

    A separate process per row on purpose: the workers import the publishers,
    which carry Playwright/Meta SDK state, and a crash or a wedged HTTP client
    in one publish must not take the sweep down with it. Failure is reported as
    ``False``, never raised -- the sweep's whole job is to keep going.

    ``track`` is derived from ``module`` rather than passed in, so the log field
    can never disagree with the worker that actually ran.
    """
    track = _TRACK_DERIVATIVE if module == DERIVATIVE_WORKER else _TRACK_SOCIAL_POST
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                module,
                id_flag,
                row_id,
                "--platform",
                platform,
            ],
            cwd=_RECIPE_PUBLISHER_ROOT,
            env={**os.environ, "BRAND_DIR": str(brand_dir)},
            capture_output=True,
            text=True,
            timeout=_WORKER_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # includes subprocess.TimeoutExpired
        # `idea_id` is kept for log/alert continuity with the pre-derivative
        # sweep; `row_id` + `track` are what actually identify the row now.
        logger.error(
            "social_posts_release_error",
            idea_id=row_id,
            row_id=row_id,
            track=track,
            platform=platform,
            error=str(exc),
        )
        return False
    if result.returncode != 0:
        logger.error(
            "social_posts_release_failed",
            idea_id=row_id,
            row_id=row_id,
            track=track,
            platform=platform,
            returncode=result.returncode,
            stderr=(result.stderr or "")[-2000:],
        )
        return False
    return True


def _sweep(
    rows: list[dict[str, Any]],
    outcomes: dict[str, int],
    *,
    module: str,
    id_flag: str,
    platform: str,
    success_key: str,
    brand_dir: Path,
) -> None:
    """Publish one pass' worth of rows, tallying into the shared `outcomes`."""
    for row in rows:
        row_id = str(row["id"])
        published = publish_one(module, id_flag, row_id, platform, brand_dir=brand_dir)
        key = success_key if published else _ERROR_KEY
        outcomes[key] = outcomes.get(key, 0) + 1


def release_due(*, brand_id: str, brand_dir: Path) -> dict[str, int]:
    """Publish everything due, in four passes: FB regular, FB derivative, then
    IG regular, IG derivative (see the module docstring for the ordering).

    The readers are looked up on their packages at call time -- never bound at
    import -- because the API/worker tests swap ``derivatives_db``'s functions
    wholesale for an in-memory fake.

    Tracks are fully independent: nothing here short-circuits, so a spotlight
    that fails to post cannot strand a regular post past its slot, or vice
    versa. Returns the per-outcome counts the caller merges into its summary.
    """
    outcomes: dict[str, int] = {}
    _sweep(
        social_post_db.list_due_for_fb(brand_id=brand_id),
        outcomes,
        module=REGULAR_WORKER,
        id_flag=_IDEA_ID_FLAG,
        platform="fb",
        success_key="fb_published",
        brand_dir=brand_dir,
    )
    _sweep(
        derivatives_db.list_due_for_fb(brand_id=brand_id),
        outcomes,
        module=DERIVATIVE_WORKER,
        id_flag=_DERIVATIVE_ID_FLAG,
        platform="fb",
        success_key="derivative_fb_published",
        brand_dir=brand_dir,
    )
    _sweep(
        social_post_db.list_due_for_ig(brand_id=brand_id),
        outcomes,
        module=REGULAR_WORKER,
        id_flag=_IDEA_ID_FLAG,
        platform="ig",
        success_key="ig_published",
        brand_dir=brand_dir,
    )
    _sweep(
        derivatives_db.list_due_for_ig(brand_id=brand_id),
        outcomes,
        module=DERIVATIVE_WORKER,
        id_flag=_DERIVATIVE_ID_FLAG,
        platform="ig",
        success_key="derivative_ig_published",
        brand_dir=brand_dir,
    )
    return outcomes
