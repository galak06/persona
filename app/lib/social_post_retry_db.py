r"""The two ``content_ideas`` transitions an image retry needs.

A sibling of ``lib.social_post_db`` for the same reason that module is a
sibling of ``lib.ideas_db``: that file is already past the file-size limit, and
these two transitions belong to a path it does not otherwise know about.

Retrying a hook image re-enters the lifecycle from the far side::

    'queued' --claim_for_recompose--> 'composing' --+--> 'queued'  (new image)
                                                    |
                                                    \-> 'queued'  (unchanged)

``'composing'`` is deliberately the SAME state a first composition uses, not a
new one. It is already the state that means "this row's image is being made",
and it is already excluded from everything that must not happen mid-flight:
``social_post_db.reject`` and ``schedule_fb`` are both guarded on ``'queued'``,
so a post cannot be approved or rejected out from under a running retry, and
the retry cannot land on a post that has since been approved. Inventing a
parallel status would have meant widening both of those guards to exclude it.

The write that ends a successful retry is ``social_post_db.set_pending_review``
-- already guarded on ``'composing'``, already the function that lands a row at
``'queued'`` with an image. The retry passes the row's EXISTING captions and
flags back into it unchanged, which is the whole contract: a retry replaces the
image and nothing else.

Defensive like its siblings (logs, never raises): a bookkeeping failure must
leave the post reviewable, never take down the run.
"""

from __future__ import annotations

import logging

from lib import db

_log = logging.getLogger(__name__)


def claim_for_recompose(idea_id: str) -> bool:
    """Atomically take ``'queued' -> 'composing'``. True if THIS call won it.

    The real single-flight guard for a retry. Two dispatches for one idea (a
    double click that raced the API's own in-flight check, or a click that
    raced the operator's other tab) both reach the worker; only the first claim
    succeeds, and the second run stops here having changed nothing.
    """
    try:
        rowcount = db.execute(
            "UPDATE content_ideas SET social_post_status = 'composing', updated_at = NOW() "
            "WHERE id = %s AND social_post_status = 'queued'",
            (idea_id,),
        )
        return rowcount > 0
    except Exception as exc:
        _log.warning("social_post_retry_db.claim_for_recompose failed: %s", exc)
        return False


def restore_queued(idea_id: str) -> bool:
    """Put a failed retry back where it started (``'composing' -> 'queued'``).

    Distinct from ``social_post_db.revert_claim``, which resets to NULL: that
    is right for a FIRST composition (the row returns to the candidate pool and
    the next run tries again), and catastrophic here. The row already carries
    reviewed captions and a usable image; NULL would drop it out of the review
    queue AND back into ``list_candidates``, where the next compose run would
    pay for a whole new plan and image and overwrite both.
    """
    try:
        rowcount = db.execute(
            "UPDATE content_ideas SET social_post_status = 'queued', updated_at = NOW() "
            "WHERE id = %s AND social_post_status = 'composing'",
            (idea_id,),
        )
        return rowcount > 0
    except Exception as exc:
        _log.warning("social_post_retry_db.restore_queued failed: %s", exc)
        return False
