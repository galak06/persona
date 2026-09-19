"""The values needed to create one ``content_derivatives`` row.

A dataclass rather than seven keyword arguments because the same bundle crosses
two seams unchanged -- the create route builds it, ``writes.insert_composing``
consumes it -- and a frozen value object is what lets a test assert "the route
asked for exactly this row" with one equality check.

Deliberately NOT here: ``id`` (minted inside ``insert_composing`` with
``uuid4``, the ``content_ideas.id`` convention), ``kind`` (one kind exists;
the insert writes ``statuses.KIND_SPOTLIGHT``) and ``status`` (a new row is
always ``'composing'`` -- there is no other legal way to enter the lifecycle).
"""

from __future__ import annotations

from dataclasses import dataclass

from lib.derivatives_db.statuses import FORMAT_FEED_POST


@dataclass(frozen=True)
class NewDerivative:
    """One product spotlight to be composed from one published post.

    ``product_asin`` and ``product_display`` are SNAPSHOTS of the catalog entry
    at creation time. The review card, the worker and the engagements log read
    them off the row, so a later catalog edit (or a discovered product aging
    out of the cache) can never orphan or silently rename a post that is
    already written.

    ``reference_category`` is the owner's optional photo-collection override,
    already slugified and proven stocked by the API. ``""`` means "let the
    plan decide".
    """

    idea_id: str
    brand_id: str
    product_key: str
    product_asin: str
    product_display: str
    format: str = FORMAT_FEED_POST
    reference_category: str = ""
