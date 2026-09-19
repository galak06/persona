"""What one spotlight composition did -- the value `compose_spotlight` returns.

Its own module for the same reason `lib.derivatives_db.new_derivative` is: the
script, the compose module and the tests all need this shape, and a dataclass
living inside `compose.py` would drag the whole crew/image import stack into
anything that merely wants to name an outcome.

`reason` is the load-bearing field. It is written into `content_derivatives.error`
by `derivatives_db.mark_failed`, printed by the script as `summary: {...}` into
`worker_runs.message`, and shown on the review card -- so the operator reading
"no_reference_photo" knows to upload a photo, and the one reading
"associates_tag_missing" knows to set an env var. A free-text message would
have told them "something went wrong" three times in three different words.

The vocabulary is frozen in the slice contract:

    not_found · not_composing · idea_not_found · no_wp_post · wp_fetch_failed
    product_not_in_catalog · associates_tag_missing · affiliate_url_failed
    plan_failed · no_reference_photo · generation_failed · overlay_failed
    write_refused · unexpected_error

plus `composed` and `dry_run` for the two ways of succeeding, and the two
reasons written by other actors: `dispatch_failed` (the API, when the queue
push fails) and `compose_timeout` (the stale sweep).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpotlightResult:
    """One composition's outcome. `ok` decides the script's exit code; `reason`
    says which of the several ways to succeed or fail actually happened.

    `image_path` is BRAND_DIR-relative (what `compose_image` returns), so the
    worker that wrote it and the API container that serves it resolve the same
    bind-mounted file. Both it and `source` stay `""` on every failure and on a
    dry run, which is the machine-readable form of "nothing was generated".
    """

    ok: bool
    reason: str
    image_path: str = ""
    source: str = ""
