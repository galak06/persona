"""Inline LLM drafting helper for engagement-comment scanners.

Called inline by ``scripts/fb_scan.py``, ``scripts/ig_scan.py`` and
``scripts/wp_scan.py`` at the queue-append site. Returns a single
voice-validated comment string ready to be dropped into
``.claude/state/comment_queue.json`` as ``draft_comment``.

Design:
  - Thin wrapper around ``lib.reply_drafter._call_gemini`` (HTTP shape) and
    ``lib.reply_drafter._VOICE_RULES`` (the brand-voice prompt block).
  - Routes the draft through ``lib.comment_generator.validate_voice`` with
    ``allow_own_url=False`` — engagement comments must never carry our URL.
  - Retries the LLM call **once** with a stricter prompt that names the
    specific voice violations from the first attempt.
  - Returns ``""`` (empty string) on any failure path so the caller can
    simply check truthiness and skip the item. Empty drafts are converted
    to ``USER_SKIPPED`` downstream by the comment approver.

All failure paths emit a structured ``log.info`` (or ``log.warning`` on the
final voice-validation failure) so the engagement log + observability stack
can attribute drops.
"""

from __future__ import annotations

import logging
import os
from typing import Literal

from lib.comment_generator import validate_voice
from lib.reply_drafter import _VOICE_RULES, _call_gemini

log = logging.getLogger(__name__)

Platform = Literal["facebook", "instagram", "wordpress"]

_MAX_TOKENS = 400


def draft_comment_for_post(
    *,
    platform: Platform,
    post_text: str,
    group_or_hashtag: str | None,
    post_url: str | None = None,
    site_context: str | None = None,
) -> str:
    """Generate a Nalla's-Dad-voice engagement comment for a scanned post.

    Returns the validated draft text (stripped), or an empty string on:
      - missing ``GEMINI_API_KEY``
      - LLM returning no candidate text
      - voice validation failing twice (one retry with stricter prompt)

    The caller is responsible for the empty case (typically: append the
    item with ``draft_comment=""`` and let the approver flip it to
    ``USER_SKIPPED``).
    """
    if not os.environ.get("GEMINI_API_KEY"):
        error_msg = "GEMINI_API_KEY environment variable is not set"
        log.error(
            {
                "event": "draft_gemini_key_missing",
                "platform": platform,
                "group_or_hashtag": group_or_hashtag,
                "error": error_msg,
            }
        )
        raise RuntimeError(error_msg)

    prompt = _build_prompt(
        platform=platform,
        post_text=post_text,
        group_or_hashtag=group_or_hashtag,
        post_url=post_url,
        site_context=site_context,
    )
    draft = _call_gemini(prompt, max_tokens=_MAX_TOKENS)
    if not draft:
        log.info(
            {
                "event": "draft_gemini_returned_none",
                "platform": platform,
                "group_or_hashtag": group_or_hashtag,
            }
        )
        return ""

    valid, violations = validate_voice(draft, allow_own_url=False)
    if valid:
        cleaned = draft.strip()
        log.info(
            {
                "event": "draft_inline_ok",
                "platform": platform,
                "len": len(cleaned),
                "attempts": 1,
            }
        )
        return cleaned

    # Retry once with a stricter prompt that calls out the violations.
    log.info(
        {
            "event": "draft_voice_retry",
            "platform": platform,
            "violations": violations,
        }
    )
    retry_prompt = (
        f"{prompt}\n\nIMPORTANT: your previous draft failed brand-voice "
        f"validation. Avoid the following violations on this rewrite: "
        f"{'; '.join(violations)}"
    )
    retry_draft = _call_gemini(retry_prompt, max_tokens=_MAX_TOKENS)
    if not retry_draft:
        log.info(
            {
                "event": "draft_gemini_retry_returned_none",
                "platform": platform,
            }
        )
        return ""

    valid, violations = validate_voice(retry_draft, allow_own_url=False)
    if valid:
        cleaned = retry_draft.strip()
        log.info(
            {
                "event": "draft_inline_ok",
                "platform": platform,
                "len": len(cleaned),
                "attempts": 2,
            }
        )
        return cleaned

    log.warning(
        {
            "event": "draft_voice_fail_final",
            "platform": platform,
            "violations": violations,
        }
    )
    return ""


def _build_prompt(
    *,
    platform: Platform,
    post_text: str,
    group_or_hashtag: str | None,
    post_url: str | None,
    site_context: str | None,
) -> str:
    """Assemble the Gemini prompt: voice rules + context + instruction."""
    parts: list[str] = [
        _VOICE_RULES.strip(),
        f"\nPLATFORM: {platform}",
    ]
    if group_or_hashtag:
        parts.append(f"GROUP/HASHTAG: {group_or_hashtag}")
    if post_url:
        parts.append(f"POST URL: {post_url}")
    parts.append(f"\nORIGINAL POST:\n{post_text.strip()}")
    if site_context:
        parts.append(
            f"\nRELEVANT SITE CONTENT (do NOT link unless natural):\n"
            f"{site_context}"
        )
    parts.append(
        "\nWrite a single short reply (1-3 sentences). Personal, helpful, "
        "no salesy language, no medical claims, no links. Output ONLY the "
        "reply text — no preamble, no quotes."
    )
    return "\n".join(parts)
