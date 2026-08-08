"""LLM-based classifiers for Facebook group admission/join status.

Two independent read-only judgment calls for ``fb-group-scout``:

  - ``classify_admission_likelihood`` — PRE-join: given the limited text a
    search-result card exposes (``EXTRACT_GROUP_CARDS_JS`` in
    ``lib.group_discovery.fb_search``), predict whether the group looks like
    it auto-accepts join requests, needs admin review, appears closed, or is
    unclear from the card alone.
  - ``classify_join_outcome`` — POST-join: given the visible text of a
    group's page after navigating to it, determine whether we now appear to
    be a member, whether the request looks declined/removed, whether it's
    still pending, or unclear.

Both go through ``lib.llm_client.TextLLM.complete_json`` against a fixed
response schema — the same schema-constrained-JSON pattern
``lib.draft_helper._llm_json`` uses with ``ENGAGE_RESPONSE_SCHEMA``.

Failure contract (mirrors ``_llm_json`` exactly): both functions return
``None`` — never raise — on an LLM call failure (missing key, non-2xx, SDK
exception; ``complete_json`` already degrades those to ``None``) OR on a
response that parses but is missing one of its required fields. ``None``
means "no classification" — callers must treat it exactly like a skip/decline,
the same way a ``None`` draft is treated as "no comment" elsewhere in this
codebase. Never treat ``None`` as any specific status value.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from lib.llm_client import LLMRequest, get_llm

logger = logging.getLogger(__name__)

ADMISSION_LIKELIHOOD_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "likelihood": {
            "type": "string",
            "enum": ["easy", "admin_approval", "closed", "unknown"],
        },
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["likelihood", "confidence", "reason"],
}

JOIN_OUTCOME_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["joined", "rejected", "still_pending", "unknown"],
        },
        "reason": {"type": "string"},
    },
    "required": ["status", "reason"],
}

_ADMISSION_LIKELIHOOD_VALUES: Final[frozenset[str]] = frozenset(
    {"easy", "admin_approval", "closed", "unknown"}
)
_JOIN_OUTCOME_STATUS_VALUES: Final[frozenset[str]] = frozenset(
    {"joined", "rejected", "still_pending", "unknown"}
)

_ADMISSION_PROMPT_TEMPLATE = """You are looking at the text scraped from ONE Facebook group's card in a
group-search results page. This is all the information available — no page
navigation, no join attempt has happened yet.

Based ONLY on this card text (member count, description, activity/post
frequency, privacy label), predict how likely a join request to this group
is to be admitted:

  - "easy": the group looks like it auto-accepts joins or has very low
    friction to get in (e.g. public, large, high activity, no screening
    language).
  - "admin_approval": the group looks like it screens join requests — admin
    review, membership questions, "request to join" framing, private group
    with vetting language.
  - "closed": the group looks like it is not currently accepting new
    members at all (explicitly closed, full, inactive/archived language).
  - "unknown": the card text does not give enough signal to tell.

CARD TEXT:
{card_text}

Respond with ONLY a JSON object — no markdown fences, no preamble, no text
before or after it:
{{"likelihood": "easy"|"admin_approval"|"closed"|"unknown", "confidence": <number 0-1>, "reason": "<one short sentence explaining the prediction>"}}"""

_JOIN_OUTCOME_PROMPT_TEMPLATE = """You are looking at the visible text of a Facebook group's page after we
navigated to it following a join (or join-request) action.

Based ONLY on this page text, determine our current membership status in
this group:

  - "joined": the page text indicates we are now a member (e.g. member-only
    UI is visible, a "leave group" / "joined" affordance, member-count-plus-
    us framing).
  - "rejected": the page text indicates our request was declined, removed,
    or we are explicitly blocked from the group.
  - "still_pending": the page text indicates the request is still awaiting
    admin review (e.g. "request pending", "submitted for approval").
  - "unknown": the page text does not give enough signal to tell.

PAGE TEXT:
{page_text}

Respond with ONLY a JSON object — no markdown fences, no preamble, no text
before or after it:
{{"status": "joined"|"rejected"|"still_pending"|"unknown", "reason": "<one short sentence explaining the determination>"}}"""

_MAX_TOKENS = 300


def classify_admission_likelihood(card_text: str) -> dict[str, Any] | None:
    """Predict pre-join admission friction from a search-result card's text.

    ``card_text`` is the scraped text for ONE candidate group — member count,
    description, activity/post-frequency, privacy label — the same fields
    ``EXTRACT_GROUP_CARDS_JS`` (``lib.group_discovery.fb_search``) produces
    per card, joined into one blob by the caller.

    Returns ``{"likelihood": "easy"|"admin_approval"|"closed"|"unknown",
    "confidence": <0-1>, "reason": <str>}``, or ``None`` on ANY failure —
    the LLM call failing (``complete_json`` already degrades that to
    ``None``), or a response missing a required field, or a ``likelihood``
    value outside the fixed enum. ``None`` means "could not classify"; it is
    NOT the same as ``"unknown"`` (a successful classification the model
    itself was unsure about) — callers must not conflate the two. Never
    raises.
    """
    if not card_text or not card_text.strip():
        return None

    response = get_llm().complete_json(
        LLMRequest(
            user=_ADMISSION_PROMPT_TEMPLATE.format(card_text=card_text.strip()),
            max_tokens=_MAX_TOKENS,
            trace_name="fb-group-admission-likelihood",
        ),
        response_schema=ADMISSION_LIKELIHOOD_SCHEMA,
    )
    if response is None:
        return None

    missing = [k for k in ("likelihood", "confidence", "reason") if k not in response]
    if missing:
        logger.warning(
            "admission-likelihood response missing fields %s: %s", missing, str(response)[:200]
        )
        return None

    if response["likelihood"] not in _ADMISSION_LIKELIHOOD_VALUES:
        logger.warning(
            "admission-likelihood response has invalid likelihood %r: %s",
            response["likelihood"],
            str(response)[:200],
        )
        return None

    if not isinstance(response["confidence"], (int, float)):
        logger.warning(
            "admission-likelihood response has non-numeric confidence: %s", str(response)[:200]
        )
        return None

    return response


def classify_join_outcome(page_text: str) -> dict[str, Any] | None:
    """Determine post-join membership status from a group page's visible text.

    ``page_text`` is the visible text scraped from a group's page after
    navigating to it following a join action — the same kind of banner/body
    text ``scripts/fb_pending_posts_check.py``'s ``_scan_feed_for_caption``
    matches against a fixed keyword list, except this classifier hands the
    text to the LLM instead so it generalizes beyond hand-enumerated phrases.

    Returns ``{"status": "joined"|"rejected"|"still_pending"|"unknown",
    "reason": <str>}``, or ``None`` on ANY failure — the LLM call failing, or
    a response missing a required field, or a ``status`` value outside the
    fixed enum. ``None`` means "could not classify"; it is NOT the same as
    ``"unknown"`` (a successful classification the model itself was unsure
    about) — callers must not conflate the two. Never raises.
    """
    if not page_text or not page_text.strip():
        return None

    response = get_llm().complete_json(
        LLMRequest(
            user=_JOIN_OUTCOME_PROMPT_TEMPLATE.format(page_text=page_text.strip()),
            max_tokens=_MAX_TOKENS,
            trace_name="fb-group-join-outcome",
        ),
        response_schema=JOIN_OUTCOME_SCHEMA,
    )
    if response is None:
        return None

    missing = [k for k in ("status", "reason") if k not in response]
    if missing:
        logger.warning(
            "join-outcome response missing fields %s: %s", missing, str(response)[:200]
        )
        return None

    if response["status"] not in _JOIN_OUTCOME_STATUS_VALUES:
        logger.warning(
            "join-outcome response has invalid status %r: %s",
            response["status"],
            str(response)[:200],
        )
        return None

    return response
