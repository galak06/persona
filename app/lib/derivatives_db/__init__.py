"""``content_derivatives``: social posts derived from an already-published post.

A sibling of ``lib.social_post_db``, not an extension of it. That module drives
ONE social set per idea through columns on ``content_ideas``; once the set is
published the idea is spent. A derivative is many-per-idea -- one row per
featured product, repeatable over time -- so it has its own table and its own
lifecycle (``db/schema.sql`` explains the table; ``statuses`` the vocabulary).

Callers use the package, never the submodules::

    from lib import derivatives_db

    new_id = derivatives_db.insert_composing(derivatives_db.NewDerivative(...))
    derivatives_db.schedule_fb(new_id, due_at=slot)

That is a contract, not a style preference: the API and worker tests replace
``derivatives_db`` on the module under test with an in-memory fake, which only
works if every call goes through this one name.

Split by responsibility to stay under the file-size limit: ``reads`` (queries),
``writes`` (create -> review -> schedule), ``publish`` (the atomic publish
claim and result writes).
"""

from __future__ import annotations

from lib.derivatives_db.new_derivative import NewDerivative
from lib.derivatives_db.publish import (
    Platform,
    claim_publish,
    release_publish_claim,
    set_fb_result,
    set_ig_result,
)
from lib.derivatives_db.reads import (
    find_active,
    get,
    last_scheduled_fb_slot,
    list_due_for_fb,
    list_due_for_ig,
    list_for_review,
)
from lib.derivatives_db.statuses import (
    ACTIVE_STATUSES,
    FAILED_VISIBLE_HOURS,
    FORMAT_FEED_POST,
    FORMATS,
    KIND_SPOTLIGHT,
    SLOT_HOLDING,
    STALE_COMPOSING_ERROR,
    STALE_COMPOSING_SECONDS,
    STATUSES,
)
from lib.derivatives_db.writes import (
    fail_stale_composing,
    insert_composing,
    mark_failed,
    reject,
    schedule_fb,
    set_pending_review,
    unschedule_fb,
)

__all__ = [
    "ACTIVE_STATUSES",
    "FAILED_VISIBLE_HOURS",
    "FORMATS",
    "FORMAT_FEED_POST",
    "KIND_SPOTLIGHT",
    "SLOT_HOLDING",
    "STALE_COMPOSING_ERROR",
    "STALE_COMPOSING_SECONDS",
    "STATUSES",
    "NewDerivative",
    "Platform",
    "claim_publish",
    "fail_stale_composing",
    "find_active",
    "get",
    "insert_composing",
    "last_scheduled_fb_slot",
    "list_due_for_fb",
    "list_due_for_ig",
    "list_for_review",
    "mark_failed",
    "reject",
    "release_publish_claim",
    "schedule_fb",
    "set_fb_result",
    "set_ig_result",
    "set_pending_review",
    "unschedule_fb",
]
