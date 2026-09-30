"""Status vocabulary of the ``content_derivatives`` table.

A leaf module on purpose. ``reads.py``, ``writes.py`` and ``publish.py`` all
need these values, and the package ``__init__`` re-exports both them and those
three modules. Defining the constants in ``__init__`` itself would make every
submodule import its own parent while the parent is still half-initialised --
it works only for as long as nobody "tidies" the constants below the imports,
and the failure mode is an ``ImportError`` that takes the whole API down at
start-up. A module that imports nothing cannot take part in a cycle.

``db/schema.sql`` carries no CHECK constraint for any of this (no table in the
schema does -- see the comment above ``content_derivatives`` there), so these
tuples ARE the vocabulary. The API validates ``?status=`` against ``STATUSES``;
the SQL in the sibling modules spells the individual values out literally, the
same way ``lib.social_post_db`` does, so each guard can be read without
chasing a constant.
"""

from __future__ import annotations

from typing import Final

#: Every value ``content_derivatives.status`` may hold, in lifecycle order.
#:
#:   (insert) composing -> queued -> scheduled -> fb_publishing -> fb_published
#:                                                -> ig_publishing -> published
#:   composing -> failed (terminal) | queued -> rejected (terminal)
#:   scheduled -> queued (unschedule)
#:   fb_publishing -> scheduled, ig_publishing -> fb_published: ONLY when the
#:   publisher raised (``publish.release_publish_claim``).
STATUSES: Final[tuple[str, ...]] = (
    "composing",
    "queued",
    "scheduled",
    "fb_publishing",
    "fb_published",
    "ig_publishing",
    "published",
    "rejected",
    "failed",
)

#: The predicate of the partial unique index ``uq_content_derivatives_active``:
#: the states in which a second row for the same (idea, product, format) would
#: be a duplicate review item. MUST stay identical to the index's WHERE clause
#: in ``db/schema.sql`` and to the ``ON CONFLICT ... WHERE`` in
#: ``writes.insert_composing`` -- Postgres only infers a partial index as the
#: conflict arbiter when the predicates match.
ACTIVE_STATUSES: Final[tuple[str, ...]] = ("composing", "queued", "scheduled")

#: Rows that hold a Facebook slot, past or future, so the next approval lands
#: strictly after the latest one. The two ``*_publishing`` claims are included:
#: a row mid-publish still owns its slot, and dropping it for those seconds
#: would let a concurrent approval take the same hour.
SLOT_HOLDING: Final[tuple[str, ...]] = (
    "scheduled",
    "fb_publishing",
    "fb_published",
    "ig_publishing",
    "published",
)

#: ``content_derivatives.format``. Only ``feed_post`` is composable in Slice 1;
#: ``carousel`` is reserved for Slice 3 so the column, the unique index and the
#: worker's ``unsupported_format`` guard do not need to change when it arrives.
FORMATS: Final[tuple[str, ...]] = ("feed_post", "carousel")
FORMAT_FEED_POST: Final[str] = "feed_post"

#: ``content_derivatives.kind`` -- the only kind that exists today.
KIND_SPOTLIGHT: Final[str] = "product_spotlight"

#: A 'composing' row older than this is a compose run that died without
#: reporting (worker killed, container recreated). Longer than the 900 s
#: dispatch timeout so a run that is merely slow is never failed under itself.
STALE_COMPOSING_SECONDS: Final[int] = 1200

#: ``error`` stamped by ``writes.fail_stale_composing``.
STALE_COMPOSING_ERROR: Final[str] = "compose_timeout"

#: How long a 'failed' row keeps showing in the review listing, so the owner
#: sees WHY a spotlight never appeared instead of a card that silently vanished.
FAILED_VISIBLE_HOURS: Final[int] = 24
